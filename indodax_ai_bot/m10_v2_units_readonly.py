"""M10-05 V2-only named user-unit audit. No service changes or secret reads.

This is NOT process enumeration, active source attestation, or egress-IP
traffic measurement. It runs systemctl --user show ONLY for fixed V2 names;
never queries Environment, ExecStart, credentials, PIDs, or unrelated units.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Callable


# Fixed allowlist. Do not discover or inspect other projects/services.
_V2_UNITS = (
    ("btc_public_ws", "indodax-v2-public-ws-btc.service"),
    ("m05_telegram", "indodax-v2-m05-telegram.service"),
    ("m05_telegram_timer", "indodax-v2-m05-telegram.timer"),
    ("m03_scanner", "indodax-v2-m03-mainpath.service"),
    ("m03_scanner_timer", "indodax-v2-m03-mainpath.timer"),
)
_FIELDS = ("LoadState", "ActiveState", "UnitFileState",
           "FragmentPath", "NeedDaemonReload")
_ALLOWED_LOAD = frozenset(("loaded", "not-found", "masked", "error"))
_ALLOWED_ACTIVE = frozenset(("active", "inactive", "failed",
                             "activating", "deactivating", "reloading"))
_ALLOWED_ENABLED = frozenset(("enabled", "disabled", "masked", "static",
                              "indirect", "generated", "transient",
                              "linked", "enabled-runtime", "linked-runtime",
                              "masked-runtime", "bad", "alias"))
_ALLOWED_RELOAD = frozenset(("yes", "no"))


def _safe_systemctl_show(unit: str) -> str | None:
    """Read safe properties of one allowlisted V2 user unit, no side effects.

    No shell, no use of -p Environment or ExecStart, no process enumeration.
    Errors are fixed-status only; stdout/stderr are never forwarded verbatim.
    """
    if unit not in {unit_name for _, unit_name in _V2_UNITS}:
        return None
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    if os.environ.get("HOME"):
        env["HOME"] = os.environ["HOME"]
    if os.environ.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = os.environ["XDG_RUNTIME_DIR"]
    else:
        env["XDG_RUNTIME_DIR"] = "/run/user/" + str(os.geteuid())
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        env["DBUS_SESSION_BUS_ADDRESS"] = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    args = ["/usr/bin/systemctl", "--user", "--no-pager", "show"]
    for field in _FIELDS:
        args.extend(("-p", field))
    args.append(unit)
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, timeout=5,
            env=env, check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if completed.returncode != 0 or len(completed.stdout) > 8192:
        return None
    return completed.stdout


def _fixed_value(value: str | None, allowed: frozenset[str]) -> str:
    return value if value in allowed else "unknown"


def _parse_fixed_properties(raw: str | None) -> dict[str, str]:
    if not isinstance(raw, str) or len(raw) > 8192:
        return {}
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name not in _FIELDS or name in fields:
            # Duplicate properties would be ambiguous; reject the whole
            # observation instead of trusting the last value.
            return {}
        fields[name] = value
    return fields


def inspect_named_v2_units(
    *, reader: Callable[[str], str | None] | None = None,
    home: Path | None = None,
) -> dict:
    """Describe only fixed V2 user-units; never infer network or quota proof."""
    get_show = reader if reader is not None else _safe_systemctl_show
    owner_home = Path.home() if home is None else Path(home)
    results = []
    for role, unit in _V2_UNITS:
        properties = _parse_fixed_properties(get_show(unit))
        load = _fixed_value(properties.get("LoadState"), _ALLOWED_LOAD)
        active = _fixed_value(properties.get("ActiveState"), _ALLOWED_ACTIVE)
        enabled = _fixed_value(properties.get("UnitFileState"), _ALLOWED_ENABLED)
        daemon_reload = _fixed_value(
            properties.get("NeedDaemonReload"), _ALLOWED_RELOAD
        )
        expected_fragment = owner_home / ".config" / "systemd" / "user" / unit
        # An exact, user V2-named fragment is necessary but NOT proof the
        # installed bytes/source SHA or any running Python process match.
        fragment_matches = (
            properties.get("FragmentPath") == str(expected_fragment)
        )
        results.append({
            "role": role,
            "observed_load": load,
            "observed_active": active,
            "observed_enabled": enabled,
            "v2_user_fragment_path_matches": fragment_matches,
            "unit_needs_daemon_reload": daemon_reload,
        })

    timer = next(row for row in results
                 if row["role"] == "m03_scanner_timer")
    observed_disabled = (
        timer["observed_load"] == "loaded"
        and timer["observed_active"] == "inactive"
        and timer["observed_enabled"] == "disabled"
        and timer["v2_user_fragment_path_matches"]
        and timer["unit_needs_daemon_reload"] == "no"
    )
    # Even if the timer is disabled, that cannot authorize a future start.
    needs_safety_review = (
        timer["observed_active"] in ("active", "activating")
        or timer["observed_enabled"] in ("enabled", "enabled-runtime")
    )
    return {
        "scope": "KNOWN_V2_USER_UNITS_ONLY",
        "status": "V2_NAMED_UNIT_METADATA_ONLY",
        "units": results,
        "m03_timer_disabled_metadata_observed": observed_disabled,
        "m03_timer_unexpected_activation_attention": needs_safety_review,
        "active_v2_source_sha_independently_verified": False,
        "all_v2_rest_clients_identified": False,
        "all_live_v2_rest_clients_same_quota_db_verified": False,
        "outbound_public_ip_independently_verified": False,
        "all_egress_ip_consumers_verified": False,
        "external_rolling_60s_peak_verified": False,
        "m10_05_full_acceptance": False,
        "m03_timer_go_authorized": False,
    }


def main() -> int:
    report = inspect_named_v2_units()
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0  # Read-only probe completed, NOT acceptance.


if __name__ == "__main__":
    raise SystemExit(main())
