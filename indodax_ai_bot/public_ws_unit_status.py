"""M04-L: read-only operator view of the STAGED public BTC user systemd unit.

This does not install, start, stop, enable, restart, or trade. systemd's
running process status is separate from PUBLIC market-data freshness and
does not upgrade any G1 issuer or paid AI authorization.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Callable

UNIT_NAME = "indodax-v2-public-ws-btc.service"
EXPECTED_PROPERTIES = (
    "LoadState", "ActiveState", "SubState", "UnitFileState",
    "MainPID", "FragmentPath",
)


def inspect_public_ws_systemd_unit(
    *, home: str | Path | None = None,
    template: str | Path | None = None,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Never write a file or perform a state-changing systemctl action."""
    root = Path(home) if home is not None else Path.home()
    source = (Path(template) if template is not None
              else Path(__file__).resolve().parents[1] / "deploy" / UNIT_NAME)
    installed = root / ".config" / "systemd" / "user" / UNIT_NAME
    base: dict[str, Any] = {
        "stage": "M04L_PUBLIC_WS_SYSTEMD_READINESS",
        "unit": UNIT_NAME,
        "status": "REJECT",
        "staged_template_present": False,
        "staged_template_safety_valid": False,
        "unit_installed": False,
        "installed_unit_matches_staged": False,
        "unit_loaded": False,
        "unit_active": False,
        "unit_enabled": False,
        "main_pid": None,
        "systemd_running_confirmed": False,
        "live_trade_stream_connected": None,
        "full_closed_minute_coverage_proven": False,
        "indodax_trade_tape_completeness_proven": False,
        "source_authenticated_for_g1": False,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
    }
    try:
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 8192:
            return {**base, "reason": "PUBLIC_WS_STAGED_UNIT_MISSING_OR_INVALID"}
        body = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {**base, "reason": "PUBLIC_WS_STAGED_UNIT_MISSING_OR_INVALID"}
    # Exactly one guard declaration each. Detect appended override lines too.
    required = (
        ("Restart", "on-success"),
        ("WorkingDirectory", "%h/indodax-ai-bot-v2"),
        ("Environment", "INDODAX_TRADING_ENABLED=NO"),
        ("Environment", "INDODAX_ALLOW_LIVE_EXECUTION=NO"),
        ("Environment", "INDODAX_EXECUTION_MODE=DRY_RUN"),
    )
    entries: list[tuple[str, str]] = []
    for line in body.splitlines():
        clean = line.strip()
        if not clean or clean.startswith(("#", ";")) or (clean.startswith("[") and clean.endswith("]")):
            continue
        if "=" not in clean:
            return {**base, "staged_template_present": True,
                    "reason": "PUBLIC_WS_STAGED_UNIT_UNSAFE"}
        name, value = clean.split("=", 1)
        entries.append((name.strip(), value.strip()))
    if (
        any(sum(1 for entry in entries if entry == item) != 1
            for item in required)
        or sum(1 for name, _ in entries if name == "Restart") != 1
        or sum(1 for name, _ in entries if name == "WorkingDirectory") != 1
        or any(name == "ExecStart" and
               (not value.startswith("%h/indodax-ai-bot-v2/.venv/bin/python -B -m indodax_ai_bot ")
                or "--pair btc_idr" not in value
                or not (
                    ("--public-ws-supervised-once" in value
                     and "--public-ws-supervised-continuous" not in value)
                    or ("--public-ws-supervised-continuous" in value
                        and "--public-ws-supervised-once" not in value
                        and ("--collector-sessions 4" in value
                             or "--collector-sessions 2" in value)
                        and "--stream-seconds 30" in value
                        and "--collector-pause-seconds 0" in value
                        and "--public-ws-reconnect-until-recovered" in value)
                ))
               for name, value in entries)
        or sum(1 for name, _ in entries if name == "ExecStart") != 1
        or any(name == "Environment" and
               (value.startswith("INDODAX_TRADING_ENABLED=") and
                value != "INDODAX_TRADING_ENABLED=NO" or
                value.startswith("INDODAX_ALLOW_LIVE_EXECUTION=") and
                value != "INDODAX_ALLOW_LIVE_EXECUTION=NO" or
                value.startswith("INDODAX_EXECUTION_MODE=") and
                value != "INDODAX_EXECUTION_MODE=DRY_RUN")
               for name, value in entries)
    ):
        return {**base, "staged_template_present": True,
                "reason": "PUBLIC_WS_STAGED_UNIT_UNSAFE"}
    base["staged_template_present"] = True
    base["staged_template_safety_valid"] = True
    if installed.is_symlink():
        return {**base, "reason": "PUBLIC_WS_INSTALLED_SYMLINK_NOT_ACCEPTED"}
    if not installed.is_file():
        return {**base, "status": "STAGED_NOT_INSTALLED",
                "reason": "PUBLIC_WS_UNIT_NOT_INSTALLED_NO_ACTIVATION"}
    base["unit_installed"] = True
    try:
        if installed.stat().st_size != len(body.encode("utf-8")) or installed.read_text(encoding="utf-8") != body:
            return {**base, "reason": "PUBLIC_WS_INSTALLED_UNIT_DIFFERS"}
    except (OSError, UnicodeError):
        return {**base, "reason": "PUBLIC_WS_INSTALLED_UNIT_INVALID"}
    base["installed_unit_matches_staged"] = True
    run = runner if runner is not None else subprocess.run
    args = [
        "systemctl", "--user", "show", UNIT_NAME, "--no-pager",
        "--property=" + ",".join(EXPECTED_PROPERTIES),
    ]
    try:
        response = run(args, capture_output=True, text=True, timeout=5, check=False)
        if response.returncode != 0 or not isinstance(response.stdout, str) or len(response.stdout) > 16384:
            return {**base, "reason": "PUBLIC_WS_USER_SYSTEMD_UNAVAILABLE"}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {**base, "reason": "PUBLIC_WS_USER_SYSTEMD_UNAVAILABLE"}
    props: dict[str, str] = {}
    for line in response.stdout.splitlines():
        if "=" not in line:
            return {**base, "reason": "PUBLIC_WS_UNIT_STATUS_INVALID"}
        key, value = line.split("=", 1)
        if key not in EXPECTED_PROPERTIES or key in props:
            return {**base, "reason": "PUBLIC_WS_UNIT_STATUS_INVALID"}
        props[key] = value
    if set(props) != set(EXPECTED_PROPERTIES):
        return {**base, "reason": "PUBLIC_WS_UNIT_STATUS_INVALID"}
    if props["FragmentPath"] != str(installed) or props["LoadState"] != "loaded":
        return {**base, "reason": "PUBLIC_WS_UNIT_FRAGMENT_OR_LOAD_MISMATCH"}
    base["unit_loaded"] = True
    base["unit_enabled"] = props["UnitFileState"] in ("enabled", "enabled-runtime")
    try:
        pid = int(props["MainPID"])
        if pid < 0:
            raise ValueError()
    except ValueError:
        return {**base, "reason": "PUBLIC_WS_UNIT_MAINPID_INVALID"}
    base["main_pid"] = pid if pid else None
    active = props["ActiveState"] == "active" and props["SubState"] == "running" and pid > 0
    base["unit_active"] = active
    base["systemd_running_confirmed"] = active
    return {
        **base,
        "status": ("USER_UNIT_RUNNING_OBSERVED" if active else "USER_UNIT_INACTIVE_OR_FAILED"),
        "reason": ("SYSTEMD_PROCESS_ONLY_NOT_STREAM_EVIDENCE" if active
                   else "NO_ACTIVE_SUPERVISED_PUBLIC_WS_PROCESS"),
    }
