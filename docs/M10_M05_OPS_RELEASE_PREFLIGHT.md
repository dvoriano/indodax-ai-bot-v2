# M10-06 | Original V2 M05 /ops release plan, PRE-DEPLOY ONLY

**Current owner evidence (4 Nov 2026):**

- V2 main on owner VPS: `da7e0ef18b09009c25fbfd7a93eab691d31a6b6b`, clean tracked files, old `409cdd8` ancestor retained. Owner inadvertently ran `git pull --ff-only`, which performed a valid fast-forward, **not** a deployment acceptance.
- Owner unit metadata: BTC public WS service `inactive/disabled`; M03 scanner timer `inactive/disabled`; M05 dedicated Telegram timer `active/enabled`. Owner did not show actual M05 service's running checkout identity or its installed Python code.
- M10-06 source check: original owner-only M05 `/ops` [PR#121](https://github.com/dvoriano/indodax-ai-bot-v2/pull/121) merged at `323c309`; original M05 poller fake Bot API tests [PR#122](https://github.com/dvoriano/indodax-ai-bot-v2/pull/122) at `5cbd4c5`; **1145/1145 GitHub** and owner **10/10 pinned VPS offline PASS**. No real Telegram `/ops` delivery observed.
- Standalone V2 read-only M10 status on owner VPS reported four BTC WS warnings: REJECT, INCOMPLETE, STALE and NOT_ACTIVE; last sidecar requested 10, attempted 5, safe 4. This does not authorize a restart.
- Installed BTC WS ExecCondition has one script token lexically outside the permitted V2 checkout. Its target remains **out of scope**, never follow/inspect, remove, bypass, or restart.

## Release boundaries

**No automatic installation or service changes are approved by this document.** This is a review checklist to avoid changing the already-running owner Telegram timer/cursor or touching any non-V2 checkout, service or other project's credentials. Never deploy or copy from V1/Hermes. Trading, private GET, paid AI, BUY/SELL/CANCEL, scanner timer and BTC WS restart stay OFF.

Source `systemd/user/indodax-v2-m05-telegram.service` in the original GitHub V2 checkout declares `Type=oneshot`, `Restart=no`, safe trading flags `NO/NO/DRY_RUN`, and an isolated Python `-I -B` entrypoint in `~/indodax-ai-bot-v2/scripts/m05_telegram_service_tick_entry.py`. Its `EnvironmentFile` names private M05 configuration, which this review must not open or print. The existing timer is `indodax-v2-m05-telegram.timer`. A Git source update does **not** prove the deployed service uses these exact bytes, source SHA, environment or ledger.

## One non-invasive release-preflight observation

If the owner requests the next step, use this **read-only metadata command**. It queries only two fixed, known V2 M05 units and converts WorkingDirectory/FragmentPath into **fixed booleans**, without echoing raw paths, ExecStart, env, secrets, journal, another checkout or process enumeration. No service change.

```bash
(
  set -euo pipefail
  "$HOME/indodax-ai-bot-v2/.venv/bin/python" -B - <<'PY'
import json
import os
from pathlib import Path
import subprocess

unit_service = "indodax-v2-m05-telegram.service"
unit_timer = "indodax-v2-m05-telegram.timer"
home = Path.home()
root = home / "indodax-ai-bot-v2"
unitdir = home / ".config" / "systemd" / "user"
keys = ("LoadState", "ActiveState", "UnitFileState",
        "FragmentPath", "WorkingDirectory", "NeedDaemonReload")

def show(name):
    args = ["/usr/bin/systemctl", "--user", "--no-pager", "show"]
    for key in keys:
        args.extend(("-p", key))
    args.append(name)
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=6, check=False)
        if p.returncode or len(p.stdout) > 8192:
            return None
        properties = {}
        for line in p.stdout.splitlines():
            k, sep, v = line.partition("=")
            if sep and k in keys:
                if k in properties:
                    return None
                properties[k] = v
        return properties
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None

def fixed(props, field, choices):
    if not isinstance(props, dict):
        return "UNKNOWN"
    value = props.get(field)
    return value if value in choices else "UNKNOWN"

service = show(unit_service)
timer = show(unit_timer)
report = {
    "scope": "TWO_FIXED_V2_M05_USER_UNITS_METADATA_ONLY",
    "service_load": fixed(service, "LoadState", ("loaded", "not-found", "masked")),
    "service_active": fixed(service, "ActiveState", ("active", "inactive", "failed")),
    "service_file_state": fixed(service, "UnitFileState",
                                ("enabled", "disabled", "static")),
    "timer_load": fixed(timer, "LoadState", ("loaded", "not-found", "masked")),
    "timer_active": fixed(timer, "ActiveState", ("active", "inactive", "failed")),
    "timer_file_state": fixed(timer, "UnitFileState",
                             ("enabled", "disabled", "static")),
    "service_named_v2_fragment_observed":
        isinstance(service, dict) and
        service.get("FragmentPath") == str(unitdir / unit_service),
    "timer_named_v2_fragment_observed":
        isinstance(timer, dict) and
        timer.get("FragmentPath") == str(unitdir / unit_timer),
    "service_working_directory_equals_original_v2_root":
        isinstance(service, dict) and
        service.get("WorkingDirectory") == str(root),
    "service_unit_daemon_reload_clean":
        isinstance(service, dict) and
        service.get("NeedDaemonReload") == "no",
    "timer_unit_daemon_reload_clean":
        isinstance(timer, dict) and
        timer.get("NeedDaemonReload") == "no",
    "installed_execstart_or_source_sha_verified": False,
    "installed_m05_ledger_cursor_unchanged_verified": False,
    "actual_telegram_owner_ops_reply_verified": False,
    "m10_06_full_acceptance": False,
    "service_modified_or_restarted": False,
    "release_authorized": False,
    "trading_authorized": False,
}
print(json.dumps(report, sort_keys=True))
PY
  echo "M10_M05_OPS_DEPLOY_PREFLIGHT_READONLY_COMPLETE"
)
```

**Fail closed:** this command never inspects ExecStart or another checkout, so even a matching WorkingDirectory is **not proof** that `/ops` is installed or that it is safe to deploy. If the working directory does not equal the original V2 root, do **not** follow or read it. Stop and ask for an explicitly scoped V2-only release decision. An inactive service with an active timer can be normal for a oneshot unit. If the M05 timer is not active, do not unilaterally start it.

## Acceptance phases that remain open

1. **Preflight**: read-only named V2 M05 unit metadata and verified expected original V2 checkout identity, without disclosing credentials. No deployment actions.
2. **Owner-approved release**: only after an explicit scoped owner authorization and rollback design that preserves the current owner ledger, Telegram cursor and lock. No copy/migration from unapproved checkouts, no second poller or token reuse in parallel. Do not reset offsets, delete database or treat source-only GO flags as authorization.
3. **Distinct real acceptance**: after approved deployment, owner manually requests `/ops` in the existing private bot, confirms a sanitized V2 four-alert response and rechecks M03 timer inactive/disabled. Do not claim delivered real alert based on fake Bot API tests. An owner-requested `/ops` reply is **not** 24/7 automatic alert delivery.
4. **M10-06 full acceptance**: separate pinned runtime startup/stop/restart recovery, genuine 24-hour heartbeat and alert-delivery observations while preserving the external-script guard. Until then M10-06 full acceptance `☐` and M10 overall **4/7**.

A rollback plan must restore *exact prior* V2 M05 unit/source wiring and preserve the existing ledger and monotonic cursor; the rollback itself must be separately authorized and must not execute as part of read-only preflight. If unit wiring depends on a path outside `~/indodax-ai-bot-v2`, STOP rather than read or migrate it.

**No action in this document changes VPS state.**
