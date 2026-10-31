"""M05 owner-only operator views sourced from THIS V2 deployment, read-only.

No V1, tokens, private Indodax requests, paid models, orders, or runner
mutations. The state of a systemd unit is NOT source freshness or G1 trust.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Callable

from .public_ws_health import public_ws_health

_M03_TIMER = "indodax-v2-m03-mainpath.timer"
_M03_SERVICE = "indodax-v2-m03-mainpath.service"
_ALLOWED_UNITS = frozenset((_M03_TIMER, _M03_SERVICE))
_PROPS = frozenset(("LoadState", "ActiveState", "UnitFileState"))
_ALLOWED_ACTIVE = frozenset(("active", "inactive", "activating", "deactivating", "failed"))
_ALLOWED_FILE = frozenset(("enabled", "enabled-runtime", "disabled", "static", "masked", "masked-runtime"))


def _unit_state(unit: str, *, runner: Callable[..., Any]) -> tuple[str, str]:
    if unit not in _ALLOWED_UNITS:
        return ("UNKNOWN", "UNKNOWN")
    try:
        response = runner(
            ["systemctl", "--user", "show", unit, "--no-pager",
             "--property=LoadState,ActiveState,UnitFileState"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if response.returncode != 0 or not isinstance(response.stdout, str) or len(response.stdout) > 4096:
            return ("UNKNOWN", "UNKNOWN")
        found: dict[str, str] = {}
        for line in response.stdout.splitlines():
            if line.count("=") != 1:
                return ("UNKNOWN", "UNKNOWN")
            key, value = line.split("=", 1)
            if key not in _PROPS or key in found:
                return ("UNKNOWN", "UNKNOWN")
            found[key] = value
        if set(found) != _PROPS or found["LoadState"] != "loaded":
            return ("UNKNOWN", "UNKNOWN")
        if found["ActiveState"] not in _ALLOWED_ACTIVE or found["UnitFileState"] not in _ALLOWED_FILE:
            return ("UNKNOWN", "UNKNOWN")
        return found["ActiveState"].upper(), found["UnitFileState"].upper()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ("UNKNOWN", "UNKNOWN")


def readonly_operator_view(
    command: str, *, now_epoch: int,
    home: Path | None = None, runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Return owner-safe text; called only AFTER original Telegram owner gate.

    No external network or private account side effects. Missing/invalid
    observations are visibly UNKNOWN, never a green/healthy claim.
    """
    if command not in ("/health", "/scanner") or type(now_epoch) is not int or now_epoch <= 0:
        raise ValueError("M05_INVALID_READONLY_OPERATOR_VIEW")
    run = subprocess.run if runner is None else runner
    base = {
        "accepted": True,
        "final_decision": "REJECT",
        "execution_allowed": False,
        "live_trading_authorized": False,
        "owner_view": "M05_" + command[1:].upper() + "_READONLY",
    }
    timer, enabled = _unit_state(_M03_TIMER, runner=run)
    if command == "/scanner":
        svc, _ = _unit_state(_M03_SERVICE, runner=run)
        return {
            **base,
            "text": (
                "V2 scanner M03 | timer=" + timer + ", enabled=" + enabled
                + ", service=" + svc + ".\n"
                "Status unit saja; bukan bukti data pasar valid atau scanner 24/7.\n"
                "Final REJECT. Trading OFF."
            ),
        }

    root = Path.home() if home is None else Path(home)
    # The EXISTING original V2 collector owns these files. Never construct
    # PublicTradeStreamJournal writable or create any SQLite schema here.
    runtime = root / "indodax-ai-bot-v2" / "runtime"
    try:
        health = public_ws_health(
            runtime / "public_ws_btc.sqlite3",
            runtime / "public_ws_btc.status.json",
            now_epoch=now_epoch,
            max_age_seconds=3600,
        )
        observed = health.get("last_batch_recent_and_consistent") is True
        last_batch = "RECENT_COMPLETED_UNATTESTED" if observed else "UNVERIFIED_OR_STALE"
    except (OSError, ValueError):
        last_batch = "UNVERIFIED_OR_STALE"
    return {
        **base,
        "text": (
            "V2 health | BTC WS last batch=" + last_batch
            + "; M03 timer=" + timer + " (" + enabled + ").\n"
            "Batch lampau bukan heartbeat/live socket dan bukan source G1 tepercaya.\n"
            "Final REJECT. Trading OFF."
        ),
    }
