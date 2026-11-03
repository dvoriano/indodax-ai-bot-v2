# M10-06 | BTC public WS exec-condition: V2-only read-only diagnosis

**Observed 4 Nov 2026:** `indodax-v2-public-ws-btc.service` has `Result=exec-condition`, `ActiveState=inactive`, `SubState=dead`, `UnitFileState=disabled`, `NRestarts=0`, `ExecMainStatus=0`. The published V2 `deploy/indodax-v2-public-ws-btc.service` has no `ExecCondition=`; the *installed* unit or V2 user drop-in needs inspection, not alteration. An earlier diagnostic printed `new_batch_completed=None`, `completed_sessions=None`, `last_batch_epoch=None`, and `recorded_discontinuities=None`, but those are **not the sidecar field names** in original V2 `indodax_ai_bot/public_ws_supervisor.py`.

**Important interpretation:** `stream_status=DISCONNECTED` is the expected safe end-of-batch state and by itself is neither PASS nor failure. For a qualifying historical batch, check `status=PUBLIC_COLLECTION_BATCH_OK_UNATTESTED`, `attempted_sessions=requested_sessions=completed_safe_sessions`, `restart_only_after_clean_batch=true`, and no failure code. `last_completed_batch_epoch` is when the sidecar was last written, **not proof the service is currently running**. `Result=exec-condition` indicates systemd evaluated an ExecCondition path, but its reason cannot be inferred from `ExecMainStatus=0` alone.

## Single read-only proof, no service mutation

This command prints **sanitized booleans/statuses only**. It reads one fixed allowlisted V2 user-unit via `systemctl --user show` and one V2 owner status JSON via `O_NOFOLLOW` bounded read. No journal, service source file, environment values, command/argv, private API, other repos, restart, credential, or trading request is output. Even if an ExecCondition value contains arguments, the script returns only **presence and numeric status codes**, never the command.

```bash
(
  set -euo pipefail
  ROOT="$HOME/indodax-ai-bot-v2"
  "$ROOT/.venv/bin/python" -B - "$ROOT/runtime/public_ws_btc.status.json" <<'PY'
import json
import os
import re
import stat
import subprocess
import sys

unit = "indodax-v2-public-ws-btc.service"
keys = (
    "Result", "LoadState", "ActiveState", "SubState", "UnitFileState",
    "ConditionResult", "AssertResult", "NeedDaemonReload",
    "ExecCondition", "DropInPaths",
)
report = {
    "scope": "BTC_WS_ONE_V2_USER_UNIT_AND_ONE_V2_STATUS_FILE",
    "no_changes_executed": True,
    "systemd_observation": "UNKNOWN",
    "batch_observation": "UNKNOWN",
    "service_started_or_restarted": False,
}
try:
    args = ["/usr/bin/systemctl", "--user", "--no-pager", "show"]
    for key in keys:
        args += ["-p", key]
    args.append(unit)
    p = subprocess.run(args, capture_output=True, text=True, timeout=8,
                       check=False)
    if p.returncode == 0 and len(p.stdout) <= 16384:
        fields = {}
        for line in p.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator and key in keys and key not in fields:
                fields[key] = value
        allowed = {
            "Result": {"success", "exec-condition", "exit-code", "signal",
                       "timeout", "condition", "resources", "start-limit-hit",
                       "protocol", "watchdog", "core-dump"},
            "LoadState": {"loaded", "not-found", "masked", "error"},
            "ActiveState": {"active", "inactive", "failed", "activating",
                            "deactivating", "reloading"},
            "SubState": {"running", "dead", "failed", "start", "stop", "exited",
                         "auto-restart"},
            "UnitFileState": {"enabled", "disabled", "static", "masked", "linked",
                              "indirect", "enabled-runtime"},
            "ConditionResult": {"yes", "no"},
            "AssertResult": {"yes", "no"},
            "NeedDaemonReload": {"yes", "no"},
        }
        report["systemd_observation"] = "V2_UNIT_METADATA_READ"
        for key, values in allowed.items():
            v = fields.get(key, "")
            report["systemd_" + key] = v if v in values else "UNKNOWN"
        condition = fields.get("ExecCondition", "")
        report["exec_condition_present"] = bool(condition.strip())
        report["exec_condition_numeric_status_codes"] = [
            int(m) for m in re.findall(r"(?<![A-Za-z])status=(\d+)", condition)[:8]
        ]
        report["has_unit_dropins"] = bool(fields.get("DropInPaths", "").strip())
except (OSError, subprocess.TimeoutExpired, ValueError):
    report["systemd_observation"] = "V2_UNIT_METADATA_UNAVAILABLE"

try:
    path = sys.argv[1]
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
                or not 2 <= info.st_size <= 65536):
            raise ValueError("V2_STATUS_UNTRUSTED")
        payload = os.read(fd, 65537)
    finally:
        os.close(fd)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("V2_STATUS_INVALID")
    report["batch_observation"] = "V2_SUPERVISOR_STATUS_PARSED"
    string_fields = (
        "status", "reason", "collector_failure_code", "stream_status",
    )
    numeric_fields = (
        "last_completed_batch_epoch", "requested_sessions",
        "attempted_sessions", "completed_safe_sessions",
    )
    for k in string_fields:
        v = data.get(k)
        if (v is None or
            (type(v) is str and len(v) <= 96
             and re.fullmatch(r"[A-Z0-9_:]+", v))):
            report["batch_" + k] = v
        else:
            report["batch_" + k] = "REDACTED_OR_INVALID"
    for k in numeric_fields:
        v = data.get(k)
        report["batch_" + k] = (
            v if type(v) is int and 0 <= v <= 10**12 else None
        )
    report["batch_restart_only_after_clean_batch"] = (
        data.get("restart_only_after_clean_batch")
        if type(data.get("restart_only_after_clean_batch")) is bool else None
    )
except (OSError, ValueError, TypeError, UnicodeError):
    report["batch_observation"] = "V2_STATUS_READ_UNAVAILABLE"

print(json.dumps(report, sort_keys=True))
PY
  echo "M10_BTC_WS_EXECCONDITION_READONLY_COMPLETE"
)
```

**After output:** if `exec_condition_present=true`, compare only the sanitized status code and presence of V2 drop-ins against the source (which has no ExecCondition); do not print shell commands or env. Any `batch_status=REJECT` is a historical last-sidecar result, not a fresh request. If information remains absent/unknown, leave M10-06 acceptance unchecked and ask the owner before any service action. M10-01 historic bounded-batch acceptance remains preserved. M03 timer and trading LIVE stay OFF.
