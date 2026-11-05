"""Owner-scoped, short-lived PUBLIC REST admission for original V2 M03.

This is NOT automatic IP-wide measurement or permission to start systemd.
External demand and inventory are operator declarations, NOT observed facts.
No service-name allowlist, no reads or writes outside the V2 owner policy.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Callable


class M03EgressAdmissionError(ValueError):
    """Redacted fixed reason, never disclose an IP, path or source payload."""


_MAX_BYTES = 8192
_MAX_AGE_SECONDS = 900
# Explicit V2-only owner-confirmed NONLIVE pilot. This is an operator
# declaration, not independent shared-IP telemetry or a trading permit.
_MAX_NONLIVE_PILOT_SECONDS = 20 * 3600
_V2_NONLIVE_SCOPE = "M03_NONLIVE_PUBLIC_PILOT"
_V2_LOCAL_REST_CAP = 90
_FIELDS = frozenset({
    "schema_version", "owner_approved_public_only",
    "all_shared_ip_consumers_accounted", "egress_public_ip",
    "external_consumer_ids", "external_peak_requests_rolling_60s",
    "issued_at_epoch", "expires_at_epoch",
})
_V2_FIELDS = _FIELDS | frozenset({"pilot_scope"})
_CONSUMER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class M03SharedEgressAdmission:
    """Conservative V2 cap: 168 minus operator-attested external peak.

    Validate before the scheduled caller and again before EACH public GET.
    This does not meter external machines, NAT peers, or other accounts.
    """

    def __init__(self, path: str | Path | None = None, *,
                 clock: Callable[[], float] | None = None) -> None:
        self.path = Path(path) if path is not None else Path(
            os.environ.get("INDODAX_M03_EGRESS_ADMISSION_FILE")
            or str(Path.home() / ".config" / "indodax-ai-bot-v2"
                   / "m03-egress-admission.json")
        )
        self.clock = clock or time.time

    def allowed_local_limit(self) -> int:
        try:
            now = self.clock()
            if (type(now) not in (int, float)
                    or not 0 < now < 1e12):
                raise M03EgressAdmissionError("M03_EGRESS_INVALID_CLOCK")
            fd = os.open(
                self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or info.st_mode & 0o077
                        or not 2 <= info.st_size <= _MAX_BYTES):
                    raise M03EgressAdmissionError("M03_EGRESS_POLICY_UNTRUSTED")
                with os.fdopen(fd, "rb", closefd=False) as file:
                    raw = file.read(_MAX_BYTES + 1)
            finally:
                os.close(fd)
            policy = json.loads(raw)
            if not isinstance(policy, dict):
                raise M03EgressAdmissionError("M03_EGRESS_POLICY_INVALID")
            version = policy.get("schema_version")
            if (type(version) is not int or version not in (1, 2)
                    or set(policy) != (_FIELDS if version == 1
                                       else _V2_FIELDS)):
                raise M03EgressAdmissionError("M03_EGRESS_POLICY_INVALID")
            if (policy["owner_approved_public_only"] is not True
                    or policy["all_shared_ip_consumers_accounted"] is not True):
                raise M03EgressAdmissionError("M03_EGRESS_UNACCOUNTED_PEERS")
            if version == 2 and policy["pilot_scope"] != _V2_NONLIVE_SCOPE:
                raise M03EgressAdmissionError("M03_EGRESS_PILOT_SCOPE_INVALID")
            if version == 2 and (
                    policy["external_consumer_ids"] != []
                    or policy["external_peak_requests_rolling_60s"] != 0):
                raise M03EgressAdmissionError("M03_EGRESS_PILOT_PEERS_PRESENT")
            ip = policy["egress_public_ip"]
            if not isinstance(ip, str) or not 1 <= len(ip) <= 45:
                raise M03EgressAdmissionError("M03_EGRESS_POLICY_INVALID")
            try:
                if not ipaddress.ip_address(ip).is_global:
                    raise ValueError("nonpublic")
            except ValueError:
                raise M03EgressAdmissionError("M03_EGRESS_POLICY_INVALID") from None
            peers = policy["external_consumer_ids"]
            if (not isinstance(peers, list) or len(peers) > 32
                    or any(type(p) is not str or _CONSUMER.fullmatch(p) is None
                           for p in peers)
                    or len(peers) != len(set(peers))):
                raise M03EgressAdmissionError("M03_EGRESS_UNACCOUNTED_PEERS")
            used = policy["external_peak_requests_rolling_60s"]
            if (type(used) is not int or not 0 <= used < 168
                    or (used > 0 and not peers)):
                raise M03EgressAdmissionError("M03_EGRESS_PEER_DEMAND_INVALID")
            issued, expires = (policy["issued_at_epoch"],
                               policy["expires_at_epoch"])
            if (type(issued) is not int or type(expires) is not int
                    or issued > now or expires <= now
                    or issued <= 0 or expires <= issued
                    or expires - issued > (_MAX_AGE_SECONDS if version == 1
                                            else _MAX_NONLIVE_PILOT_SECONDS)):
                raise M03EgressAdmissionError("M03_EGRESS_ATTESTATION_EXPIRED")
            return min(_V2_LOCAL_REST_CAP, 168 - used) if version == 2 else 168 - used
        except M03EgressAdmissionError:
            raise
        except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
            raise M03EgressAdmissionError("M03_EGRESS_POLICY_UNAVAILABLE") from None
