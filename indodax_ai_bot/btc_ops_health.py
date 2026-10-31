"""M10 BTC public-only operator health, read-only and non-authorizing.

Never accesses Indodax/private APIs, Telegram token, paid AI, or order path.
The output is an allowlisted status/alert classification, not raw journal,
systemd output, credentials or an authenticated G1 receipt.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time
from typing import Any, Callable

ROOT = Path.home() / "indodax-ai-bot-v2" / "runtime"
SERVICE = "indodax-v2-public-ws-btc.service"
TIMER = "indodax-v2-public-ws-btc.timer"
SERVICE_SHOW = ("LoadState", "ActiveState", "Result", "Restart",
                "MainPID", "NRestarts", "UnitFileState")
TIMER_SHOW = ("LoadState", "ActiveState", "UnitFileState")
_SAFE_STR = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _private_json(path: Path, cap: int) -> dict[str, Any]:
    st = path.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
            or st.st_mode & 0o077 or st.st_size < 2 or st.st_size > cap):
        raise ValueError("UNSAFE_PRIVATE_FILE")
    with path.open("rb") as fh:
        raw = fh.read(cap + 1)
    if len(raw) > cap:
        raise ValueError("OVERSIZED_PRIVATE_FILE")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("INVALID_PRIVATE_JSON")
    return data


def _systemd(name: str, runner: Callable[..., Any]) -> dict[str, str]:
    if name not in (SERVICE, TIMER):
        raise ValueError("SYSTEMD_UNKNOWN_UNIT")
    fields = SERVICE_SHOW if name == SERVICE else TIMER_SHOW
    p = runner(
        ["systemctl", "--user", "show", name, "--no-pager",
         "--property=" + ",".join(fields)],
        capture_output=True, text=True, timeout=7, check=False,
    )
    if p.returncode != 0 or not isinstance(p.stdout, str) or len(p.stdout) > 8192:
        raise ValueError("SYSTEMD_UNAVAILABLE")
    data: dict[str, str] = {}
    for line in p.stdout.splitlines():
        if "=" not in line:
            raise ValueError("SYSTEMD_INVALID")
        k, v = line.split("=", 1)
        if k not in fields or k in data or len(v) > 200:
            raise ValueError("SYSTEMD_INVALID")
        data[k] = v
    if set(data) != set(fields):
        raise ValueError("SYSTEMD_INCOMPLETE")
    return data


def _journal(path: Path) -> tuple[int, int]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("JOURNAL_MISSING")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                         uri=True, timeout=3) as con:
        con.execute("PRAGMA query_only=ON")
        publications = con.execute(
            "SELECT COUNT(*) FROM ws_trade_publications WHERE pair='btc_idr'"
        ).fetchone()[0]
        gaps = con.execute(
            "SELECT COUNT(*) FROM ws_stream_discontinuities WHERE pair='btc_idr'"
        ).fetchone()[0]
    if not all(type(x) is int and x >= 0 for x in (publications, gaps)):
        raise ValueError("JOURNAL_INVALID")
    return publications, gaps


def health(*, root: Path = ROOT, now_epoch: int | None = None,
           runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Read only the existing ORIGINAL service, timer and BTC journal.

    Warnings are sticky priority: no owner GO / expired, systemd fault, gap
    growth, bad prior batch, stale data. Never invent availability/alerts sent.
    """
    result: dict[str, Any] = {
        "stage": "M10_ORIGINAL_BTC_PUBLIC_OPERATIONS_HEALTH",
        "status": "ALERT_REVIEW",
        "reason": "PUBLIC_BTC_HEALTH_UNAVAILABLE",
        "pair": "btc_idr",
        "last_batch_age_seconds": None,
        "go_remaining_seconds": None,
        "publications": None,
        "historical_gaps": None,
        "gap_budget": None,
        "timer_active": False,
        "collector_active": False,
        "restart_disabled": False,
        "read_only": True,
        "notification_sent": False,
        "source_authenticated_for_g1": False,
        "full_trade_tape_proven": False,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
        "final_decision": "REJECT",
    }

    try:
        now = int(time.time()) if now_epoch is None else now_epoch
        if type(now) is not int or now <= 0:
            raise ValueError("INVALID_TIME")

        svc = _systemd(SERVICE, runner)
        timer = _systemd(TIMER, runner)
        result["timer_active"] = (
            timer["LoadState"] == "loaded"
            and timer["ActiveState"] == "active"
            and timer["UnitFileState"] in ("enabled", "enabled-runtime")
        )
        result["restart_disabled"] = svc["Restart"] == "no"
        result["collector_active"] = svc["ActiveState"] in (
            "active", "activating", "deactivating",
        )
        if svc["LoadState"] != "loaded" or timer["LoadState"] != "loaded":
            raise ValueError("SYSTEMD_NOT_LOADED")
        if (svc["Restart"] != "no" or not result["timer_active"]
                or svc["UnitFileState"] not in ("disabled", "static")):
            raise ValueError("M10_TIMER_OR_RESTART_UNSAFE")
        if (svc["ActiveState"] not in ("inactive", "active", "activating", "deactivating")
                or svc["Result"] not in ("success", "exec-condition")):
            raise ValueError("M10_SERVICE_FAILURE")

        if (root / "btc_public_ops.hold.json").exists() or (
                root / "btc_public_ops.hold.json").is_symlink():
            raise ValueError("M10_OPERATOR_HOLD")

        go = _private_json(root / "btc_public_ops.go.json", 2048)
        if go.get("scope") != "BTC_PUBLIC_MONITOR_ONLY":
            raise ValueError("M10_PUBLIC_GO_SCOPE_UNSAFE")
        expire, budget = go.get("expires_at_epoch"), go.get("max_gap_count")
        if (type(expire) is not int or type(budget) is not int
                or budget < 0 or budget > 100000):
            raise ValueError("M10_PUBLIC_GO_INVALID")
        result["go_remaining_seconds"] = max(0, expire - now)
        result["gap_budget"] = budget
        if now >= expire:
            raise ValueError("M10_PUBLIC_GO_EXPIRED")

        publications, gaps = _journal(root / "public_ws_btc.sqlite3")
        result["publications"] = publications
        result["historical_gaps"] = gaps
        if gaps > budget:
            raise ValueError("M10_PUBLIC_GAP_BUDGET_EXCEEDED")

        sidecar = _private_json(root / "public_ws_btc.status.json", 32768)
        if (sidecar.get("status") != "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED"
                or sidecar.get("attempted_sessions") != 10
                or sidecar.get("completed_safe_sessions") != 10
                or sidecar.get("collector_failure_code") is not None
                or sidecar.get("g1_receipt_issuance_allowed") is not False
                or sidecar.get("g2_provider_call_allowed") is not False
                or sidecar.get("execution_allowed") is not False
                or type(sidecar.get("unrecoverable_gap_rebaseline_count")) is not int
                or sidecar["unrecoverable_gap_rebaseline_count"] != gaps):
            raise ValueError("M10_PUBLIC_SIDECAR_UNSAFE")

        ts = sidecar.get("last_completed_batch_epoch")
        if type(ts) is not int or ts <= 0 or ts > now:
            raise ValueError("M10_PUBLIC_BATCH_CLOCK_INVALID")
        result["last_batch_age_seconds"] = now - ts
        if result["last_batch_age_seconds"] > 1800:
            raise ValueError("M10_PUBLIC_LAST_BATCH_STALE")

        if svc["ActiveState"] in ("active", "activating", "deactivating"):
            result["status"] = "COLLECTING_UNATTESTED"
            result["reason"] = "M10_BOUNDED_BATCH_IN_PROGRESS"
        else:
            result["status"] = "HEALTHY_LAST_BATCH_UNATTESTED"
            result["reason"] = "M10_RECENT_PUBLIC_BATCH_CONSISTENT"
    except (OSError, ValueError, TypeError, json.JSONDecodeError,
            sqlite3.Error, subprocess.TimeoutExpired) as exc:
        code = str(exc)
        result["reason"] = code if _SAFE_STR.fullmatch(code) else "PUBLIC_BTC_HEALTH_UNAVAILABLE"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only BTC public ops health")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    out = health(root=args.root)
    print(json.dumps(out, sort_keys=True, separators=(",", ":")))
    return 0 if out["status"] in ("HEALTHY_LAST_BATCH_UNATTESTED",
                                  "COLLECTING_UNATTESTED") else 3


if __name__ == "__main__":
    raise SystemExit(main())
