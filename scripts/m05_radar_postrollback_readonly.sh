#!/usr/bin/env bash
# Owner READ ONLY diagnosis after M05 radar opt-in rollback.
# Do not print Telegram tokens, raw messages, owner DB or service.env.
set -euo pipefail
cd "$HOME/indodax-ai-bot-v2"
UNIT=indodax-v2-m05-telegram.service
TIMER=indodax-v2-m05-telegram.timer
DROP="$HOME/.config/systemd/user/$UNIT.d/radar-momentum.conf"

echo "===== ORIGINAL SOURCE ====="
git rev-parse --short HEAD
echo "===== ORIGINAL M05 STATE ====="
systemctl --user show "$UNIT" --no-pager \
  -p ActiveState -p SubState -p MainPID -p NRestarts \
  -p Result -p ExecMainStatus -p ExecMainStartTimestamp \
  -p NeedDaemonReload -p DropInPaths
echo "===== LEGACY TIMER STATE ====="
systemctl --user show "$TIMER" --no-pager -p ActiveState -p UnitFileState
if [[ -e "$DROP" || -L "$DROP" ]]; then
  echo RADAR_DROPIN_PRESENT=YES
else
  echo RADAR_DROPIN_PRESENT=NO
fi

PID="$(systemctl --user show "$UNIT" -p MainPID --value)"
PID="$PID" python3 - <<'PY'
import os
from pathlib import Path
pid = os.environ["PID"]
out = "UNKNOWN"
if pid.isdecimal() and int(pid) > 0:
    try:
        env = set(Path("/proc/" + pid + "/environ").read_bytes().split(b"\0"))
        out = str(b"M05_RADAR_MOMENTUM_ALERT_GO=YES" in env)
        safe = all(x in env for x in (
            b"INDODAX_TRADING_ENABLED=NO",
            b"INDODAX_ALLOW_LIVE_EXECUTION=NO",
            b"INDODAX_EXECUTION_MODE=DRY_RUN",
        ))
        print("M05_RUNNING_NO_NO_DRY_RUN=", safe)
    except OSError:
        pass
print("M05_RUNNING_RADAR_ENABLED=", out)
PY
echo "===== RECENT SANITIZED M05 JOURNAL EVIDENCE ====="
journalctl --user -u "$UNIT" --no-pager -o json -n 2500 |
python3 -c '
import collections, datetime, json, re, sys
from zoneinfo import ZoneInfo
wib = ZoneInfo("Asia/Jakarta")
failure = collections.Counter()
events = []
beats = []
for line in sys.stdin:
    try:
        row = json.loads(line)
        msg = row.get("MESSAGE", "")
        if not isinstance(msg, str):
            continue
        stamp = int(row.get("__REALTIME_TIMESTAMP", "0"))
        when = datetime.datetime.fromtimestamp(
            stamp / 1000000, wib
        ).strftime("%d %b %H:%M:%S WIB")
        pid = str(row.get("_PID", "UNKNOWN"))
    except (ValueError, TypeError, OverflowError, OSError):
        continue
    if "Scheduled restart job" in msg:
        failure["SYSTEMD_RESTART_SCHEDULED"] += 1
        events.append((when, "SYSTEMD_RESTART_SCHEDULED"))
    elif "Main process exited" in msg:
        failure["MAIN_PROCESS_EXIT"] += 1
        events.append((when, "MAIN_PROCESS_EXIT"))
    elif "Failed with result" in msg:
        failure["UNIT_FAILED"] += 1
        events.append((when, "UNIT_FAILED"))
    elif "Traceback (most recent call last)" in msg:
        failure["PYTHON_TRACEBACK"] += 1
        events.append((when, "PYTHON_TRACEBACK"))
    elif re.fullmatch(r"(M05|M10|TELEGRAM)_[A-Z0-9_]+", msg.strip()):
        failure[msg.strip()] += 1
        events.append((when, msg.strip()))
    if msg.startswith("{"):
        try:
            obj = json.loads(msg)
        except (ValueError, TypeError):
            continue
        if obj.get("status") == "M05_FAST_LOOP_HEARTBEAT_NONLIVE":
            allow = (
                "batches", "radar_enabled", "radar_invocations",
                "radar_send_acks", "radar_delivery_uncertain",
                "radar_last_status", "ops_alert_failures",
                "execution_allowed",
            )
            clean = {k: obj.get(k) for k in allow}
            beats.append((when, pid, clean))
print("FAILURE_COUNTS=", dict(failure))
print("===== RECENT PROCESS EVENTS =====")
for item in events[-15:]:
    print("EVENT", *item)
print("===== RECENT M05 HEARTBEATS =====")
for when, pid, payload in beats[-15:]:
    print("HEARTBEAT", when, "PID=" + pid, json.dumps(
        payload, sort_keys=True, separators=(",", ":")))
print("TOTAL_HEARTBEATS_IN_JOURNAL_WINDOW=", len(beats))
'
echo "READ_ONLY_DIAGNOSIS_COMPLETE"
echo "NO_RESTART"
echo "NO_DATABASE_CHANGES"
echo "TRADING_OFF"
