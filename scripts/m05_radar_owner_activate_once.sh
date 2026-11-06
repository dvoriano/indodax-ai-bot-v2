#!/usr/bin/env bash
# Owner GO 2026-11-06: ONE original M05 radar opt-in restart; NO orders.
set -Eeuo pipefail

UNIT=indodax-v2-m05-telegram.service
TIMER=indodax-v2-m05-telegram.timer
ROOT="$HOME/indodax-ai-bot-v2"
SRC=systemd/user/indodax-v2-m05-telegram.service.d/radar-momentum.conf
DROP="$HOME/.config/systemd/user/$UNIT.d/radar-momentum.conf"
OWNER_ENV="$HOME/.config/indodax-ai-bot-v2/m05/service.env"
SWITCHED=NO
RESTARTED=NO
BACKUP=NOT_CREATED
fail() { echo "M05_RADAR_PREFLIGHT_BLOCKED:$1" >&2; return 1; }
rollback() {
  rc=$?
  trap - ERR
  echo "M05_RADAR_ROLLBACK_STARTED" >&2
  if [[ "$SWITCHED" == YES ]]; then
    rm -f -- "$DROP" || true
    systemctl --user daemon-reload || true
    if [[ "$RESTARTED" == YES ]]; then
      systemctl --user restart "$UNIT" || true
    fi
  fi
  printf 'BACKUP=%s\nTRADING_OFF\n' "$BACKUP" >&2
  exit "$rc"
}
trap rollback ERR

[[ "$M05_OWNER_RADAR_SINGLE_RESTART_GO" == YES ]] ||
  fail EXPLICIT_OWNER_GO_REQUIRED
export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN
cd "$ROOT"
[[ "$(git branch --show-current)" == main ]] || fail NOT_MAIN
[[ -z "$(git status --porcelain)" ]] || fail DIRTY_REPO
git merge-base --is-ancestor ddafe5dae13b77f3e99aac452fdbfdfcc77bb964 HEAD ||
  fail M05_PR203_NOT_INSTALLED
git merge-base --is-ancestor 0c49f91d9dd1592e4e04795fb3a1996df37880c9 HEAD ||
  fail SCALPING_PR204_NOT_INSTALLED
[[ -f "$SRC" && ! -e "$DROP" && ! -L "$DROP" ]] ||
  fail UNEXPECTED_RADAR_DROPIN
grep -Fxq 'Environment=M05_RADAR_MOMENTUM_ALERT_GO=YES' "$SRC" ||
  fail UNREVIEWED_RADAR_FLAG
[[ -f "$OWNER_ENV" && ! -L "$OWNER_ENV" ]] || fail OWNER_ENV_INVALID
if grep -Eq '^[[:space:]]*M05_RADAR_MOMENTUM_ALERT_GO[[:space:]]*=' "$OWNER_ENV"; then
  fail OWNER_ENV_RADAR_OVERRIDE_PRESENT
fi
[[ "$(systemctl --user is-enabled "$TIMER" 2>/dev/null || true)" == disabled ]] ||
  fail OLD_TIMER_ENABLED
[[ "$(systemctl --user is-active "$TIMER" 2>/dev/null || true)" == inactive ]] ||
  fail OLD_TIMER_ACTIVE
[[ "$(systemctl --user is-active "$UNIT" 2>/dev/null || true)" == active ]] ||
  fail M05_NOT_RUNNING
[[ "$(systemctl --user show "$UNIT" -p Type --value)" == simple ]] ||
  fail NOT_EXISTING_LONGPOLL
[[ "$(systemctl --user show "$UNIT" -p WorkingDirectory --value)" == "$ROOT" ]] ||
  fail WRONG_SOURCE_WORKDIR
[[ "$(systemctl --user show "$UNIT" -p NeedDaemonReload --value)" == no ]] ||
  fail PENDING_UNREVIEWED_RELOAD
systemctl --user show "$UNIT" -p ExecStart --value |
  grep -Fq scripts/m05_telegram_service_tick_entry.py ||
  fail WRONG_ORIGINAL_ENTRYPOINT
systemctl --user show "$UNIT" -p DropInPaths --value |
  grep -Fq fast-longpoll.conf || fail FAST_LONGPOLL_DROPIN_MISSING
PID="$(systemctl --user show "$UNIT" -p MainPID --value)"
[[ "$PID" =~ ^[1-9][0-9]*$ && -r "/proc/$PID/environ" ]] ||
  fail PID_UNREADABLE
AGE="$(ps -o etimes= -p "$PID" | tr -d ' ')"
[[ "$AGE" =~ ^[0-9]+$ && "$AGE" -ge 90 ]] ||
  fail RECENT_M05_RESTART_REVIEW_REQUIRED

# Only print boolean policy state; never leak process env or token.
check_env() {
  M05_PID="$1" M05_RADAR_EXPECT="$2" python3 - <<'PY'
import os
from pathlib import Path
pid = os.environ["M05_PID"]
expected = os.environ["M05_RADAR_EXPECT"]
env = set(Path("/proc/" + pid + "/environ").read_bytes().split(b"\0"))
def flag(key, val):
    return (key + "=" + val).encode() in env
ok = (
    flag("INDODAX_TRADING_ENABLED", "NO")
    and flag("INDODAX_ALLOW_LIVE_EXECUTION", "NO")
    and flag("INDODAX_EXECUTION_MODE", "DRY_RUN")
    and flag("M05_TELEGRAM_FAST_LONGPOLL_GO", "YES")
    and (
        flag("M05_RADAR_MOMENTUM_ALERT_GO", "YES")
        if expected == "YES"
        else not flag("M05_RADAR_MOMENTUM_ALERT_GO", "YES")
    )
)
print("POLICY_AND_RADAR_ENV_VERIFIED=" + str(ok))
raise SystemExit(0 if ok else 2)
PY
}
check_env "$PID" NO || fail ORIGINAL_PROCESS_ENV_UNSAFE

echo "===== TARGETED ORIGINAL SOURCE TESTS (NONLIVE) ====="
.venv/bin/python -B -m unittest -q \
  tests.test_m05_fast_longpoll_original \
  tests.test_m05_original_radar_momentum_alert \
  tests.test_m05_radar_turnover_delta \
  tests.test_m03_scalping_evidence_continuation
[[ "$PID" == "$(systemctl --user show "$UNIT" -p MainPID --value)" ]] ||
  fail SPONTANEOUS_RESTART_DURING_PREFLIGHT

# Secure audit snapshot; do not copy service.env, token or owner SQLite.
install -d -m 0700 "$HOME/.local/state/indodax-ai-bot-v2"
BACKUP="$(mktemp -d "$HOME/.local/state/indodax-ai-bot-v2/m05-radar-go-XXXXXXXX")"
chmod 0700 "$BACKUP"
git rev-parse HEAD > "$BACKUP/source-sha.txt"
systemctl --user show "$UNIT" -p MainPID -p NRestarts \
  -p ActiveState -p SubState -p ExecMainStartTimestamp -p DropInPaths \
  > "$BACKUP/service-before.txt"
echo RADAR_DROPIN_ABSENT > "$BACKUP/radar-config-before.txt"
for old in fast-longpoll.conf m10-auto-alert.conf; do
  existing="$HOME/.config/systemd/user/$UNIT.d/$old"
  if [[ -f "$existing" && ! -L "$existing" ]]; then
    cp -p "$existing" "$BACKUP/$old"
  fi
done

install -d -m 0700 "$(dirname "$DROP")"
install -m 0644 "$SRC" "$DROP"
SWITCHED=YES
systemctl --user daemon-reload
RESTARTED=YES
systemctl --user restart "$UNIT"
[[ "$(systemctl --user is-active "$UNIT")" == active ]] ||
  fail M05_FAILED_START
NEWPID="$(systemctl --user show "$UNIT" -p MainPID --value)"
[[ "$NEWPID" =~ ^[1-9][0-9]*$ && "$NEWPID" != "$PID" ]] ||
  fail ORIGINAL_PROCESS_NOT_REPLACED
check_env "$NEWPID" YES || fail RADAR_FLAG_NOT_IN_RUNNING_PROCESS

# Observe the real callback heartbeat. The original receiver checks radar
# after its first successful poll, then on a monotonic 60-second cadence.
# Slow network/preflight/outbox calls can still delay that synchronous loop.
# A failed observation still rolls back; this does NOT authorize a retry.
# Only inspect allowlisted JSON heartbeat fields, never raw journal lines.
HEARTBEAT=""
for iteration in $(seq 1 140); do
  [[ "$NEWPID" == "$(systemctl --user show "$UNIT" -p MainPID --value)" ]] ||
    fail M05_UNEXPECTED_RESTART
  HEARTBEAT="$(journalctl --user -u "$UNIT" -n 500 \
    --no-pager -o json | M05_PID="$NEWPID" python3 -c '
import json,os,sys
pid=os.environ["M05_PID"]
last=None
for line in sys.stdin:
    try:
        row=json.loads(line)
        if str(row.get("_PID")) != pid:
            continue
        obj=json.loads(row.get("MESSAGE",""))
        if obj.get("status") == "M05_FAST_LOOP_HEARTBEAT_NONLIVE":
            last=obj
    except (ValueError,TypeError):
        continue
if last:
    safe=("batches","radar_enabled","radar_invocations","radar_send_acks",
          "radar_delivery_uncertain","radar_last_status",
          "ops_alert_failures","execution_allowed")
    print(json.dumps({k:last.get(k) for k in safe},sort_keys=True))
' )"
  if [[ -n "$HEARTBEAT" ]]; then
    STATE="$(HB="$HEARTBEAT" python3 -c '
import json,os
r=json.loads(os.environ["HB"])
if r.get("radar_enabled") is not True or r.get("execution_allowed") is not False:
    print("UNSAFE")
elif type(r.get("radar_invocations")) is int and r["radar_invocations"]>=1:
    print("RADAR_INVOKED")
else:
    print("HEARTBEAT_ONLY")
')"
    [[ "$STATE" != UNSAFE ]] || fail UNSAFE_HEARTBEAT
    [[ "$STATE" != RADAR_INVOKED ]] || break
  fi
  sleep 3
done
[[ -n "$HEARTBEAT" ]] || fail NO_M05_HEARTBEAT_AFTER_RESTART
if [[ "$STATE" != RADAR_INVOKED ]]; then
  echo "M05_LAST_SANITIZED_HEARTBEAT=$HEARTBEAT" >&2
  fail RADAR_CALLBACK_NOT_OBSERVED
fi
printf 'RADAR_HEARTBEAT=%s\n' "$HEARTBEAT"
printf 'SOURCE_SHA=%s\n' "$(git rev-parse HEAD)"
printf 'BACKUP=%s\n' "$BACKUP"
echo M05_RADAR_RUNTIME_ENABLED
echo WATCH_TELEGRAM_PRIVATE_SEND_ACK_ONLY_WHEN_RADAR_SEND_ACKS_GT_ZERO
echo OWNER_CHAT_SCREENSHOT_STILL_REQUIRED_FOR_RECEIPT
echo TRADING_OFF
trap - ERR
