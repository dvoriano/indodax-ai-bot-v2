"""Sanitized, LOCAL-ONLY original M03 urllib/proxy diagnostic.

Does NOT call Indodax, DNS, Telegram, private APIs, shell scripts, or
read environment-file contents. All environment/proxy VALUES remain secret.
A service metadata snapshot is not proof of actual WAF behavior or egress.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping
from urllib.request import getproxies

_UNIT = "indodax-v2-m03-mainpath.service"
_KEY_RE = re.compile(r"(?<![A-Za-z0-9_])(https?_proxy|all_proxy|no_proxy)=", re.I)
_KEYS = frozenset(("http_proxy", "https_proxy", "all_proxy", "no_proxy"))


def _proxy_names(keys) -> list[str]:
    """Report only allowlisted variable NAMES; never values/URLs/credentials."""
    return sorted({str(key).lower() for key in keys
                   if isinstance(key, str) and key.lower() in _KEYS})


def _unit_proxy_names(text: str) -> list[str]:
    return _proxy_names(match.group(1) for match in _KEY_RE.finditer(text))


def _manager_proxy_names(text: str) -> list[str]:
    return _proxy_names(
        line.split("=", 1)[0].strip() for line in text.splitlines()
        if "=" in line
    )


def inspect_local_proxy_snapshot(
    *, process_env: Mapping[str, str],
    urllib_proxy_schemes: list[str],
    manager_output: str | None,
    unit_output: Mapping[str, str] | None,
    home: Path,
) -> dict[str, Any]:
    """Pure presentation with no untrusted values or raw paths in report."""
    manager_keys = (_manager_proxy_names(manager_output)
                    if manager_output is not None else None)
    unit_keys = (_unit_proxy_names(unit_output.get("Environment", ""))
                 if unit_output is not None else None)
    pass_keys = (_unit_proxy_names(
        unit_output.get("PassEnvironment", "").replace(" ", "=")
    ) if unit_output is not None else None)
    unset_keys = (_unit_proxy_names(
        unit_output.get("UnsetEnvironment", "").replace(" ", "=")
    ) if unit_output is not None else None)
    fragment = unit_output.get("FragmentPath", "") if unit_output else ""
    actual = home / ".config" / "systemd" / "user" / _UNIT
    exec_text = unit_output.get("ExecStart", "") if unit_output else ""
    expected_checkout = str(home / "indodax-ai-bot-v2")
    # An environment file is not opened or echoed. This remains UNKNOWN.
    environment_files = (unit_output.get("EnvironmentFiles", "").strip()
                         if unit_output is not None else "")
    return {
        "status": "M03_HTTP_LOCAL_PROXY_METADATA_READONLY",
        "process_proxy_keys": _proxy_names(process_env),
        "urllib_proxy_schemes": sorted(
            {x.lower() for x in urllib_proxy_schemes if x.lower() in
             ("http", "https", "all", "no")}
        ),
        "manager_proxy_keys": manager_keys,
        "unit_explicit_proxy_keys": unit_keys,
        "unit_pass_proxy_keys": pass_keys,
        "unit_unset_proxy_keys": unset_keys,
        "unit_environment_file_present": (
            bool(environment_files) if unit_output is not None else None
        ),
        "unit_fragment_matches_original_v2": (
            fragment == str(actual) if unit_output is not None else None
        ),
        "unit_exec_uses_original_v2_checkout": (
            expected_checkout + "/.venv/bin/python" in exec_text
            if unit_output is not None else None
        ),
        "unit_active_state": (
            unit_output.get("ActiveState", "UNKNOWN")
            if unit_output is not None and unit_output.get("ActiveState") in
            ("active", "inactive", "failed", "activating", "deactivating")
            else "UNKNOWN"
        ),
        "observation_is_not_service_process_environment": True,
        "http_403_root_cause_verified": False,
        "network_calls": 0,
        "indodax_requests": 0,
        "order_sent": False,
        "trading_enabled": False,
    }


def _run_output(cmd: list[str]) -> str | None:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=8, check=False
        )
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None


def diagnose_original_m03_local() -> dict[str, Any]:
    # Read manager and fixed ORIGINAL V2 unit metadata only. No unit restart.
    manager = _run_output(["systemctl", "--user", "show-environment"])
    props = ("Environment", "PassEnvironment", "UnsetEnvironment",
             "EnvironmentFiles", "FragmentPath", "ExecStart", "ActiveState")
    raw = _run_output(
        ["systemctl", "--user", "show", _UNIT, "--no-pager"] +
        ["-p", p for p in props]
    )
    unit: dict[str, str] | None = None
    if raw is not None:
        unit = {}
        for line in raw.splitlines():
            key, sep, value = line.partition("=")
            if sep and key in props:
                unit[key] = value
    try:
        urllib_schemes = list(getproxies())
    except (OSError, ValueError):
        urllib_schemes = []
    return inspect_local_proxy_snapshot(
        process_env=os.environ,
        urllib_proxy_schemes=urllib_schemes,
        manager_output=manager,
        unit_output=unit,
        home=Path.home(),
    )


if __name__ == "__main__":
    print(json.dumps(diagnose_original_m03_local(), sort_keys=True))
