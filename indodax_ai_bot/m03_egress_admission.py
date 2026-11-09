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
_STANDING_PUBLIC_SCOPE = "M03_STANDING_PUBLIC_ONLY"
_V2_LOCAL_REST_CAP = 90
_FIELDS = frozenset({
    "schema_version", "owner_approved_public_only",
    "all_shared_ip_consumers_accounted", "egress_public_ip",
    "external_consumer_ids", "external_peak_requests_rolling_60s",
    "issued_at_epoch", "expires_at_epoch",
})
_V2_FIELDS = _FIELDS | frozenset({"pilot_scope"})
_STANDING_FIELDS = (_FIELDS - {"expires_at_epoch"}) | {"pilot_scope"}
_CONSUMER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class M03SharedEgressAdmission:
    """Conservative V2 cap: 168 minus operator-attested external peak.

    Validate before the scheduled caller and again before EACH public GET.
    This does not meter external machines, NAT peers, or other accounts.
    """

    def __init__(self, path: str | Path | None = None, *,
                 clock: Callable[[], float] | None = None,
                 btc_runtime_dir: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else Path(
            os.environ.get("INDODAX_M03_EGRESS_ADMISSION_FILE")
            or str(Path.home() / ".config" / "indodax-ai-bot-v2"
                   / "m03-egress-admission.json")
        )
        self.clock = clock or time.time
        self.btc_runtime_dir = (Path(btc_runtime_dir)
            if btc_runtime_dir is not None else
            Path.home() / "indodax-ai-bot-v2" / "runtime")

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
            if (type(version) is not int or version not in (1, 2, 3)
                    or set(policy) != (_FIELDS if version == 1
                                       else _V2_FIELDS if version == 2
                                       else _STANDING_FIELDS)):
                raise M03EgressAdmissionError("M03_EGRESS_POLICY_INVALID")
            if (policy["owner_approved_public_only"] is not True
                    or policy["all_shared_ip_consumers_accounted"] is not True):
                raise M03EgressAdmissionError("M03_EGRESS_UNACCOUNTED_PEERS")
            if version == 2 and policy["pilot_scope"] != _V2_NONLIVE_SCOPE:
                raise M03EgressAdmissionError("M03_EGRESS_PILOT_SCOPE_INVALID")
            if version == 3 and policy["pilot_scope"] != _STANDING_PUBLIC_SCOPE:
                raise M03EgressAdmissionError("M03_EGRESS_STANDING_SCOPE_INVALID")
            if version in (2, 3) and (
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
            issued = policy["issued_at_epoch"]
            if version == 3:
                # No arbitrary consent expiry, but reject stale/corrupt BTC
                # market evidence. All quota and identity rules still apply.
                if type(issued) is not int or not 0 < issued <= now:
                    raise M03EgressAdmissionError("M03_EGRESS_STANDING_INVALID")
                self._check_standing_btc(now)
                return min(_V2_LOCAL_REST_CAP, 168 - used)
            expires = policy["expires_at_epoch"]
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

    def _check_standing_btc(self, now: float) -> None:
        """Require fresh clean BTC source BEFORE each M03 public REST GET.

        Freshness is a data-quality rule, NOT a time limit on owner consent.
        Only existing local original V2 files/SQLite; no network or writes.
        """
        from .btc_ops_gate import (
            AdmissionDenied, STANDING_MODE, _private_json, _journal_gap_count,
        )
        rt = self.btc_runtime_dir
        try:
            hold = rt / "btc_public_ops.hold.json"
            if hold.exists() or hold.is_symlink():
                raise M03EgressAdmissionError("M03_STANDING_BTC_SOURCE_UNHEALTHY")
            go, _ = _private_json(rt / "btc_public_ops.go.json", 2048)
            status, _ = _private_json(rt / "public_ws_btc.status.json", 32768)
            gaps = _journal_gap_count(rt / "public_ws_btc.sqlite3")
        except AdmissionDenied:
            raise M03EgressAdmissionError("M03_STANDING_BTC_SOURCE_UNHEALTHY") from None
        issued = go.get("issued_at_epoch")
        limit = go.get("max_gap_count")
        epoch = status.get("last_completed_batch_epoch")
        observed = status.get("unrecoverable_gap_rebaseline_count")
        legacy = (
            status.get("status") == "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED"
            and status.get("requested_sessions") == 10
            and status.get("attempted_sessions") == 10
            and status.get("completed_safe_sessions") == 10
            and status.get("stream_status") == "DISCONNECTED"
            and status.get("restart_only_after_clean_batch") is True
        )
        expected = {
            "M04W_PERSISTENT_PUBLIC_WS_FOUR_WINDOWS": 4,
            "M04W_PERSISTENT_PUBLIC_WS_TWO_WINDOWS": 2,
        }.get(status.get("stage"))
        persistent = (
            status.get("status") == "PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED"
            and expected in (2, 4)
            and status.get("requested_sessions") == expected
            and status.get("attempted_sessions") == expected
            and status.get("completed_safe_sessions") == expected
            and (expected != 2 or (
                status.get("checkpoint_is_exchange_closed_1m_candle") is False
                and status.get("calendar_minute_alignment_proven") is False))
            and status.get("stream_status") in (
                "BASELINE_ONLY", "CONTIGUOUS_OBSERVED")
            and status.get("persistent_socket_observed_within_windows") is True
            and status.get("restart_only_after_clean_batch") is False
        )
        # The new 4x30s report is a public-source freshness check only, NOT
        # verified trade-tape completeness or independent G1/AI authority.
        freshness = 360 if persistent else 3600
        if (set(go) != {"scope", "mode", "issued_at_epoch", "max_gap_count"}
                or go.get("scope") != "BTC_PUBLIC_MONITOR_ONLY"
                or go.get("mode") != STANDING_MODE
                or type(issued) is not int or not 0 < issued <= now
                or type(limit) is not int or not 0 <= limit <= 100000
                or not (legacy or persistent)
                or status.get("collector_failure_code") is not None
                or status.get("g1_receipt_issuance_allowed") is not False
                or status.get("g2_provider_call_allowed") is not False
                or status.get("execution_allowed") is not False
                or type(epoch) is not int or not 0 <= now - epoch <= freshness
                or type(observed) is not int or observed != gaps
                or gaps > limit):
            raise M03EgressAdmissionError("M03_STANDING_BTC_SOURCE_UNHEALTHY")
