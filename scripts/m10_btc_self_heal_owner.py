"""Original V2 owner opt-in for bounded BTC PUBLIC transient self-heal.

Staging consent does not remove a HOLD, start service, renew a policy, place
an exchange order or spawn an extra timer. The original ExecCondition
performs only source-qualified recovery at a naturally scheduled run.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import time

from indodax_ai_bot.btc_ops_gate import (
    AdmissionDenied, ROOT, SCOPE, STANDING_MODE, _private_json,
)
from indodax_ai_bot.m10_btc_self_heal import (
    CONSENT, CONSENT_SCOPE, GO, MAX_PER_24H, MIN_INTERVAL,
    STANDING_RECONNECT_MAX_PER_24H, STANDING_RECONNECT_MIN_INTERVAL,
    AutoHealDenied, _consent, _owned_dir, _owned_safe_ancestor_dir,
    diagnose_self_heal_readonly,
)
from scripts.m10_btc_owner_bounded_recovery import (
    _units, assess_original_btc_systemd,
    BtcRecoveryDenied, DROP, TEMPLATE,
)

OWNER_TOKEN = "OWNER_ENABLE_ORIGINAL_V2_PUBLIC_TRANSIENT_SELF_HEAL"
RECONNECT_TOKEN = "OWNER_ENABLE_ORIGINAL_V2_PUBLIC_STANDING_RECONNECT"


def _upgrade_private_consent(*, consent: Path, previous: bytes,
                             updated: dict, now: int) -> None:
    """Upgrade only a verified owner-owned legacy consent, with 0600 backup.

    Service/GO/HOLD/SQLite are never modified, and a failed upgrade keeps the
    old consent available. This helper has no network or systemctl start.
    """
    tag = str(now) + "." + str(os.getpid())
    backup = consent.with_name(consent.name + ".legacy." + tag + ".bak")
    stage = consent.with_name(consent.name + ".reconnect." + tag + ".tmp")
    current = _private_json(consent, 2048)[1]
    if current != previous or backup.exists() or stage.exists():
        raise AutoHealDenied("AUTO_HEAL_OPTIN_CHANGED_OR_BACKUP_CONFLICT")
    payload = (json.dumps(updated, sort_keys=True) + "\n").encode("ascii")
    try:
        for path, raw in ((backup, previous), (stage, payload)):
            fd = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        if _private_json(consent, 2048)[1] != previous:
            raise AutoHealDenied("AUTO_HEAL_OPTIN_CHANGED_OR_BACKUP_CONFLICT")
        os.replace(stage, consent)
    except OSError:
        raise AutoHealDenied("AUTO_HEAL_OPTIN_PRIVATE_WRITE_DENIED") from None
    finally:
        stage.unlink(missing_ok=True)


def enable_owner_optin(*, now: int | None = None,
                       consent: Path = CONSENT, go: Path = GO,
                       standing_reconnect: bool = False) -> dict:
    now = int(time.time()) if now is None else now
    if type(now) is not int or now <= 0:
        raise AutoHealDenied("AUTO_HEAL_CLOCK_INVALID")
    # The runtime and ~/.config are owner-controlled ancestors; on Linux
    # they may be 0755. They must never be writable by another user.
    # The final consent directory remains 0700 and file remains 0600.
    if not _owned_safe_ancestor_dir(ROOT):
        raise AutoHealDenied("AUTO_HEAL_RUNTIME_DIRECTORY_UNTRUSTED")
    if (not _owned_dir(consent.parent)
            or not _owned_safe_ancestor_dir(consent.parent.parent)
            or consent.is_symlink()):
        raise AutoHealDenied("AUTO_HEAL_OWNER_CONFIG_DIRECTORY_UNTRUSTED")
    try:
        approval, _ = _private_json(go, 2048)
    except AdmissionDenied:
        raise AutoHealDenied("AUTO_HEAL_STANDING_GO_UNAVAILABLE") from None
    if (set(approval) != {"scope", "mode", "issued_at_epoch",
                         "max_gap_count"}
            or approval.get("scope") != SCOPE
            or approval.get("mode") != STANDING_MODE
            or type(approval.get("issued_at_epoch")) is not int
            or not 0 < approval["issued_at_epoch"] <= now
            or type(approval.get("max_gap_count")) is not int
            or not 0 <= approval["max_gap_count"] <= 100000):
        raise AutoHealDenied("AUTO_HEAL_STANDING_GO_NOT_APPROVED")
    try:
        if (DROP.is_symlink() or TEMPLATE.is_symlink()
                or not DROP.is_file() or not TEMPLATE.is_file()
                or DROP.read_bytes() != TEMPLATE.read_bytes()):
            raise AutoHealDenied("AUTO_HEAL_EXISTING_DROPIN_UNVERIFIED")
        # Confirms a SINGLE original BTC collector, original owner
        # ExecCondition+ExecStopPost, unchanged capped drop-in, NO restarts,
        # existing timer active and same M05 service still active.
        assess_original_btc_systemd(values=_units())
    except BtcRecoveryDenied:
        raise AutoHealDenied("AUTO_HEAL_ORIGINAL_SYSTEMD_UNVERIFIED") from None
    cap = (STANDING_RECONNECT_MAX_PER_24H if standing_reconnect
           else MAX_PER_24H)
    interval = (STANDING_RECONNECT_MIN_INTERVAL if standing_reconnect
                else MIN_INTERVAL)
    version = 2 if standing_reconnect else 1
    if consent.exists():
        old = _consent(now, consent)
        if standing_reconnect and old["schema_version"] == 1:
            raw = _private_json(consent, 2048)[1]
            updated = {
                "schema_version": 2,
                "scope": CONSENT_SCOPE,
                "owner_authorized": True,
                "issued_at_epoch": now,
                "max_attempts_per_24h": cap,
                "minimum_interval_seconds": interval,
            }
            _upgrade_private_consent(
                consent=consent, previous=raw, updated=updated, now=now
            )
            return {
                "status": "M10_PUBLIC_STANDING_RECONNECT_CONSENT_UPGRADED",
                "max_attempts_per_24h": cap,
                "minimum_interval_seconds": interval,
                "prior_owner_consent_backed_up": True,
                "service_started_or_restarted": False,
                "hold_removed": False,
                "order_sent": False,
            }
        # Never implicitly downgrade a stronger explicit owner permission.
        return {
            "status": "M10_PUBLIC_AUTO_HEAL_OWNER_OPTIN_ALREADY_PRESENT",
            "max_attempts_per_24h": old["max_attempts_per_24h"],
            "minimum_interval_seconds": old["minimum_interval_seconds"],
            "service_started_or_restarted": False,
            "hold_removed": False,
            "order_sent": False,
        }
    data = {
        "schema_version": version,
        "scope": CONSENT_SCOPE,
        "owner_authorized": True,
        "issued_at_epoch": now,
        "max_attempts_per_24h": cap,
        "minimum_interval_seconds": interval,
    }
    fd = -1
    try:
        fd = os.open(consent, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as output:
            fd = -1
            json.dump(data, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        raise AutoHealDenied("AUTO_HEAL_OPTIN_PRIVATE_WRITE_DENIED") from None
    finally:
        if fd >= 0:
            os.close(fd)
    return {
        "status": ("M10_PUBLIC_STANDING_RECONNECT_OPTED_IN"
                   if standing_reconnect else "M10_PUBLIC_AUTO_HEAL_OPTED_IN"),
        "scope": "ONE_EXISTING_ORIGINAL_BTC_PUBLIC_TIMER",
        "max_attempts_per_24h": cap,
        "minimum_interval_seconds": interval,
        "new_timers_or_scanners_installed": False,
        "service_started_or_restarted": False,
        "hold_removed": False,
        "trading_enabled": False,
        "order_sent": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Original V2 BTC PUBLIC safe self-heal")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--diagnose-readonly", action="store_true")
    group.add_argument("--owner-enable-standing-transient", action="store_true")
    group.add_argument("--owner-enable-standing-reconnect", action="store_true")
    p.add_argument("--owner-confirm", type=str)
    args = p.parse_args()
    if args.diagnose_readonly:
        print(json.dumps(diagnose_self_heal_readonly(), sort_keys=True))
        return 0
    required_token = (RECONNECT_TOKEN if args.owner_enable_standing_reconnect
                      else OWNER_TOKEN)
    if args.owner_confirm != required_token:
        print(json.dumps({"status": "DENIED",
            "reason": "AUTO_HEAL_EXPLICIT_OWNER_OPTIN_REQUIRED"}, sort_keys=True))
        return 2
    try:
        report = enable_owner_optin(
            standing_reconnect=args.owner_enable_standing_reconnect,
        )
    except AutoHealDenied as exc:
        # Only expose fixed local diagnostic codes, never private paths,
        # policy contents, HTTP errors or model credentials.
        code = str(exc)
        reason = code if re.fullmatch(r"AUTO_HEAL_[A-Z0-9_]{1,90}", code) else (
            "AUTO_HEAL_OWNER_SOURCE_OR_SYSTEMD_NOT_READY"
        )
        print(json.dumps({"status": "DENIED", "reason": reason},
                         sort_keys=True))
        return 2
    except (OSError, ValueError):
        print(json.dumps({"status": "DENIED",
            "reason": "AUTO_HEAL_OWNER_SOURCE_OR_SYSTEMD_NOT_READY"}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
