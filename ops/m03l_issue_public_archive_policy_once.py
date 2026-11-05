"""Owner-issued, separate M03-L PUBLIC OHLC historical backfill lease.

Only original V2 PUBLIC historical research. Does not renew or edit the
scanner's own m03-egress-admission.json, and never depends on unrelated
BTC public WS monitor GO/status (the archive GET does not consume BTC WS).

This uses the same strict existing M03SharedEgressAdmission schema v2,
no new HTTP adapter, no extra REST quota or paid AI. The owner explicitly
confirms the previous all-egress-consumers declaration remains current;
that is an owner assertion, not independent gateway traffic evidence.

Only the child process whose environment explicitly points at the NEW
archive-only file can use this lease. The original installed scanner unit
continues to use its original owner policy path and cannot acquire this
fresh research lease through this issuer.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import stat
import time
from typing import Callable

from indodax_ai_bot.m03_egress_admission import (
    M03EgressAdmissionError, M03SharedEgressAdmission,
)
from scripts.m03_egress_nonlive_pilot import _owner_policy_bytes, _read_ip

CANONICAL_ROOT = Path.home() / "indodax-ai-bot-v2"
SCANNER_POLICY = (
    Path.home() / ".config" / "indodax-ai-bot-v2"
    / "m03-egress-admission.json"
)
ARCHIVE_POLICY = SCANNER_POLICY.with_name(
    "m03l-public-archive-admission.json"
)
LEASE_SECONDS = 20 * 3600
EXPECTED_ORIGINAL_KEYS = {
    "schema_version", "owner_approved_public_only",
    "all_shared_ip_consumers_accounted", "egress_public_ip",
    "external_consumer_ids", "external_peak_requests_rolling_60s",
    "issued_at_epoch", "expires_at_epoch",
}
EXPECTED_PILOT_SCOPE = "M03_NONLIVE_PUBLIC_PILOT"


class M03LArchivePolicyDenied(ValueError):
    """Only nonsecret reason codes are exposed."""


def _canonical_private_dir(path: Path) -> None:
    if path.is_symlink():
        raise M03LArchivePolicyDenied("M03L_ARCHIVE_POLICY_DIRECTORY_UNTRUSTED")
    try:
        info = path.lstat()
    except OSError:
        raise M03LArchivePolicyDenied("M03L_ARCHIVE_POLICY_DIRECTORY_MISSING") from None
    if (not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077):
        raise M03LArchivePolicyDenied("M03L_ARCHIVE_POLICY_DIRECTORY_UNTRUSTED")


def _owner_no_external_peers_declared(
    *, original: Path, ip_reader: Callable[[], str],
) -> str:
    try:
        policy = json.loads(_owner_policy_bytes(original))
        if (type(policy) is not dict
                or type(policy.get("schema_version")) is not int
                or policy["schema_version"] not in (1, 2)
                or set(policy) != (
                    EXPECTED_ORIGINAL_KEYS if policy["schema_version"] == 1
                    else EXPECTED_ORIGINAL_KEYS | {"pilot_scope"}
                )
                or policy["owner_approved_public_only"] is not True
                or policy["all_shared_ip_consumers_accounted"] is not True
                or policy["external_consumer_ids"] != []
                or type(policy["external_peak_requests_rolling_60s"]) is not int
                or policy["external_peak_requests_rolling_60s"] != 0
                or (policy["schema_version"] == 2
                    and policy["pilot_scope"] != EXPECTED_PILOT_SCOPE)):
            raise M03LArchivePolicyDenied(
                "M03L_PREVIOUS_OWNER_NO_PEERS_DECLARATION_INVALID"
            )
        previous_ip = policy["egress_public_ip"]
        if (type(previous_ip) is not str
                or not ipaddress.ip_address(previous_ip).is_global):
            raise M03LArchivePolicyDenied(
                "M03L_PREVIOUS_OWNER_EGRESS_IP_INVALID"
            )
        # The old policy can legitimately be expired; only its preserved
        # owner no-peer assertion is carried forward upon NEW confirmation.
        current_ip = ip_reader().strip()
        if (current_ip != previous_ip
                or not ipaddress.ip_address(current_ip).is_global):
            raise M03LArchivePolicyDenied("M03L_EGRESS_PUBLIC_IP_CHANGED")
        return current_ip
    except M03LArchivePolicyDenied:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise M03LArchivePolicyDenied(
            "M03L_PREVIOUS_OWNER_EGRESS_DECLARATION_UNAVAILABLE"
        ) from None


def issue_archive_policy_once(
    *, root: Path = CANONICAL_ROOT,
    scanner_policy: Path = SCANNER_POLICY,
    archive_policy: Path = ARCHIVE_POLICY,
    epoch: int,
    owner_confirmed: bool,
    ip_reader: Callable[[], str] = _read_ip,
) -> dict:
    """Private atomic lease file, unchanged old policy, no BTC / Indodax GET.

    This function is called ONLY by explicit owner-approved CLI. A separate
    archive-only file is never referenced by the original M03 systemd unit.
    """
    if owner_confirmed is not True:
        raise M03LArchivePolicyDenied("M03L_OWNER_CONFIRMATION_REQUIRED")
    if type(epoch) is not int or not 0 < epoch < 10_000_000_000:
        raise M03LArchivePolicyDenied("M03L_OWNER_CLOCK_INVALID")
    if (root != CANONICAL_ROOT or root.is_symlink()
            or not root.is_dir()
            or scanner_policy != SCANNER_POLICY
            or archive_policy != ARCHIVE_POLICY):
        raise M03LArchivePolicyDenied("M03L_ONLY_CANONICAL_V2_POLICY")
    if (archive_policy == scanner_policy
            or archive_policy.parent != scanner_policy.parent):
        raise M03LArchivePolicyDenied("M03L_POLICY_NOT_SEPARATE")
    _canonical_private_dir(scanner_policy.parent)
    public_ip = _owner_no_external_peers_declared(
        original=scanner_policy, ip_reader=ip_reader,
    )
    if archive_policy.exists() or archive_policy.is_symlink():
        try:
            # Existing archive lease must remain owner-private and non-symlink.
            _owner_policy_bytes(archive_policy)
        except (OSError, ValueError):
            raise M03LArchivePolicyDenied(
                "M03L_EXISTING_ARCHIVE_POLICY_UNTRUSTED"
            ) from None
    source = {
        "schema_version": 2,
        "owner_approved_public_only": True,
        "all_shared_ip_consumers_accounted": True,
        "egress_public_ip": public_ip,
        "external_consumer_ids": [],
        "external_peak_requests_rolling_60s": 0,
        "issued_at_epoch": epoch,
        "expires_at_epoch": epoch + LEASE_SECONDS,
        "pilot_scope": EXPECTED_PILOT_SCOPE,
    }
    data = (json.dumps(source, sort_keys=True) + "\n").encode("ascii")
    temp = archive_policy.with_name(
        archive_policy.name + "." + str(epoch) + "." + str(os.getpid()) + ".tmp"
    )
    try:
        fd = os.open(
            temp,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, archive_policy)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    # Reuse ORIGINAL V2 issuer/verifier exactly, without changing its
    # cryptographic assumptions, scanner admission or shared quota.
    admitted = M03SharedEgressAdmission(
        archive_policy, clock=lambda: epoch
    ).allowed_local_limit()
    return {
        "status": "M03L_PUBLIC_ARCHIVE_ONLY_LEASE_PREPARED",
        "scope": "ORIGINAL_V2_M03L_PUBLIC_HISTORICAL_OHLC_ONLY",
        "archive_egress_policy_current": True,
        "original_scanner_egress_policy_modified": False,
        "local_get_cap_rolling_60s": admitted,
        "lease_valid_seconds_at_issue": LEASE_SECONDS,
        "outside_ip_peers_independently_measured": False,
        "btc_ws_go_required_for_ohlc_archive": False,
        "paid_ai_calls": 0,
        "order_sent": False,
        "execution_allowed": False,
        "final_decision": "REJECT",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Original V2 owner-confirmed M03-L PUBLIC archive-only egress lease"
    )
    parser.add_argument(
        "--owner-confirm-v2-only-public-backfill",
        action="store_true",
        help="Owner confirms prior no-external-peer declaration remains true",
    )
    args = parser.parse_args(argv)
    if (args.owner_confirm_v2_only_public_backfill is not True
            or os.environ.get("M03L_OWNER_PACED_20D_GO") != "YES"
            or os.environ.get("INDODAX_TRADING_ENABLED") != "NO"
            or os.environ.get("INDODAX_ALLOW_LIVE_EXECUTION") != "NO"
            or os.environ.get("INDODAX_EXECUTION_MODE") != "DRY_RUN"):
        print(json.dumps({
            "status": "STOP",
            "reason": "M03L_OWNER_CONFIRMATION_OR_NONLIVE_GO_REQUIRED",
            "execution_allowed": False,
            "final_decision": "REJECT",
        }, sort_keys=True))
        return 3
    try:
        report = issue_archive_policy_once(
            epoch=int(time.time()), owner_confirmed=True,
        )
    except (M03LArchivePolicyDenied, M03EgressAdmissionError, OSError) as exc:
        reason = str(exc)
        if not reason.startswith(("M03L_", "M03_")):
            reason = "M03L_ARCHIVE_LEASE_ISSUE_FAILED"
        print(json.dumps({
            "status": "STOP", "reason": reason,
            "execution_allowed": False, "final_decision": "REJECT",
        }, sort_keys=True))
        return 3
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
