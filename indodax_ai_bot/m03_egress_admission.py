"""Fail-closed M03 PUBLIC REST admission for shared-egress operators.

This is an operator-controlled, short-lived peer-demand declaration, NOT
automatic IP-wide traffic measurement or exchange proof. Do not claim shared
IP safety unless *every* external consumer has actually been accounted for.
Only the original M03 systemd scanner opts into this gate; other public V2
clients retain their previous default until separately migrated.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import stat
import time
from typing import Callable


class M03EgressAdmissionError(ValueError):
    """Static safe error code, never a path, IP, token or raw policy."""


_REQUIRED_PEERS = frozenset({
    "crypto-bot-view.service",
    "crypto-bot-telegram.service",
    "hermes-gateway.service",
})
_MAX_FILE_BYTES = 8192
_MAX_POLICY_AGE = 900
_REQUIRED_FIELDS = frozenset({
    "schema_version", "owner_approved_public_only",
    "all_shared_ip_consumers_accounted", "egress_public_ip",
    "peer_service_names", "external_peak_requests_rolling_60s",
    "issued_at_epoch", "expires_at_epoch",
})


class M03SharedEgressAdmission:
    """Validate a private short-lived declaration before EVERY M03 GET.

    Local capacity is 168 minus the operator-accounted worst-case request
    demand from non-V2 peers in the same 60-second window. The existing
    shared V2 SQLite budget then atomically enforces the resulting lower cap
    on each M03 GET. Neither layer measures V1 or external traffic itself.
    """

    def __init__(self, path: str | Path | None = None, *,
                 clock: Callable[[], float] | None = None) -> None:
        self.path = Path(path) if path is not None else Path(
            os.environ.get(
                "INDODAX_M03_EGRESS_ADMISSION_FILE",
                str(Path.home() / ".config" / "indodax-ai-bot-v2"
                    / "m03-egress-admission.json"),
            )
        )
        self.clock = clock or time.time

    def allowed_local_limit(self) -> int:
        try:
            now = self.clock()
            if type(now) not in (int, float) or not 0 < now < 1e12:
                raise M03EgressAdmissionError("M03_EGRESS_INVALID_CLOCK")

            # Never follow a symlink; read at most a small, owner-only file.
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or info.st_mode & 0o077
                        or info.st_size < 2
                        or info.st_size > _MAX_FILE_BYTES):
                    raise M03EgressAdmissionError("M03_EGRESS_POLICY_UNTRUSTED")
                with os.fdopen(fd, "rb", closefd=False) as f:
                    raw = f.read(_MAX_FILE_BYTES + 1)
            finally:
                os.close(fd)

            policy = json.loads(raw)
            if not isinstance(policy, dict) or set(policy) != _REQUIRED_FIELDS:
                raise M03EgressAdmissionError("M03_EGRESS_POLICY_INVALID")
            if (type(policy["schema_version"]) is not int
                    or policy["schema_version"] != 1
                    or policy["owner_approved_public_only"] is not True
                    or policy["all_shared_ip_consumers_accounted"] is not True):
                raise M03EgressAdmissionError("M03_EGRESS_UNACCOUNTED_PEERS")
            ip = policy["egress_public_ip"]
            if not isinstance(ip, str) or len(ip) > 45:
                raise M03EgressAdmissionError("M03_EGRESS_POLICY_INVALID")
            try:
                if not ipaddress.ip_address(ip).is_global:
                    raise ValueError("not public")
            except ValueError:
                raise M03EgressAdmissionError("M03_EGRESS_POLICY_INVALID") from None
            peers = policy["peer_service_names"]
            if (not isinstance(peers, list) or len(peers) > 32
                    or any(not isinstance(p, str) or len(p) > 80
                           for p in peers)
                    or len(peers) != len(set(peers))
                    or not _REQUIRED_PEERS.issubset(peers)):
                raise M03EgressAdmissionError("M03_EGRESS_UNACCOUNTED_PEERS")
            used = policy["external_peak_requests_rolling_60s"]
            if type(used) is not int or not 0 <= used < 168:
                raise M03EgressAdmissionError("M03_EGRESS_PEER_DEMAND_INVALID")
            issued = policy["issued_at_epoch"]
            expiry = policy["expires_at_epoch"]
            if (type(issued) is not int or type(expiry) is not int
                    or issued > now + 5 or expiry <= now
                    or issued > now or expiry - issued > _MAX_POLICY_AGE
                    or expiry <= issued):
                raise M03EgressAdmissionError("M03_EGRESS_ATTESTATION_EXPIRED")
            return 168 - used
        except M03EgressAdmissionError:
            raise
        except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
            raise M03EgressAdmissionError("M03_EGRESS_POLICY_UNAVAILABLE") from None
