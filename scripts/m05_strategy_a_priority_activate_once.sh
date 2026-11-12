#!/usr/bin/env bash
# Original M05 priority-only single restart, explicit owner GO, NO trading.
set -Eeuo pipefail
umask 077

UNIT=indodax-v2-m05-telegram.service
M03=indodax-v2-m03-mainpath.timer
ROOT="$HOME/indodax-ai-bot-v2"
SRC="systemd/user/$UNIT.d/strategy-a-priority.conf"
DROP_DIR="$HOME/.config/systemd/user/$UNIT.d"
DROP="$DROP_DIR/strategy-a-priority.conf"
OWNER_ENV="$HOME/.config/indodax-ai-bot-v2/m05/service.env"
PREVIOUS_MAIN="f2f3a5d7d10a422eb23db415b8e867b34a5e574a"
CUTOVER=NO
RESTART_ATTEMPTED=NO
SUCCESS=NO

fail() { printf 'M05_STRATEGY_A_PRIORITY_BLOCKED:%s\n' "$1" >&2; exit 3; }
rollback() {
  rc=$?
  trap - EXIT
  if [[ "$SUCCESS" != YES && "$CUTOVER" == YES ]]; then
    echo M05_STRATEGY_A_PRIORITY_ROLLBACK_STARTED >&2
    rm -f -- "$DROP" || echo ROLLBACK_DROP_REMOVE_FAILED >&2
    systemctl --user daemon-reload ||
      echo ROLLBACK_DAEMON_RELOAD_FAILED >&2
    if [[ "$RESTART_ATTEMPTED" == YES ]]; then
      systemctl --user restart "$UNIT" ||
        echo ROLLBACK_ORIGINAL_M05_RESTART_FAILED >&2
    fi
    if systemctl --user is-active --quiet "$UNIT"; then
      echo M05_ROLLBACK_SERVICE_ACTIVE >&2
    else
      echo M05_ROLLBACK_SERVICE_NEEDS_MANUAL_REVIEW >&2
    fi
    echo M05_PRIORITY_PREVIOUS_CONFIG_RESTORED_ATTEMPTED >&2
  fi
  exit "$rc"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "${M05_STRATEGY_A_PRIORITY_SINGLE_RESTART_GO:-NO}" == YES ]] || fail EXPLICIT_SINGLE_RESTART_GO_REQUIRED
export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN
cd "$ROOT" || fail SOURCE_ROOT_NOT_FOUND
[[ "$(git branch --show-current)" == main ]] || fail WRONG_GIT_BRANCH
[[ -z "$(git status --porcelain)" ]] || fail DIRTY_REPO
git merge-base --is-ancestor "$PREVIOUS_MAIN" HEAD ||
  fail REQUIRED_PRIOR_V2_SOURCE_MISSING

# Only the exact reviewed flag is allowed in the new drop-in.
[[ -f "$SRC" && ! -L "$SRC" ]] || fail UNREVIEWED_DROPIN_SOURCE
cmp -s "$SRC" <(printf '%s\n' '[Service]' \
  'Environment=M05_RADAR_PRIORITY_V2_GO=YES') ||
  fail UNEXPECTED_DROPIN_CONTENT

[[ -d "$DROP_DIR" && ! -L "$DROP_DIR" ]] ||
  fail ORIGINAL_M05_SYSTEMD_DIRECTORY_UNSAFE
[[ "$(stat -c %u "$DROP_DIR")" == "$(id -u)" ]] ||
  fail SYSTEMD_DIRECTORY_NOT_OWNED
[[ "$(stat -c %a "$DROP_DIR")" == 700 ]] ||
  fail SYSTEMD_DIRECTORY_NOT_PRIVATE
[[ ! -e "$DROP" && ! -L "$DROP" ]] ||
  fail PRIORITY_DROPIN_ALREADY_EXISTS

[[ -f "$OWNER_ENV" && ! -L "$OWNER_ENV" ]] ||
  fail ORIGINAL_M05_ENV_UNAVAILABLE
[[ "$(stat -c %u "$OWNER_ENV")" == "$(id -u)" ]] ||
  fail ORIGINAL_M05_ENV_WRONG_OWNER
[[ "$(stat -c %a "$OWNER_ENV")" == 600 ]] ||
  fail ORIGINAL_M05_ENV_UNSAFE_MODE
if grep -Eq '^[[:space:]]*M05_RADAR_PRIORITY_V2_GO[[:space:]]*=' "$OWNER_ENV"; then
  fail PRIORITY_ENVFILE_OVERRIDE_PRESENT
fi

# Original PRE-FLIGHT verifies *running* process flags NO/NO/DRY_RUN,
# longpoll, radar+WS3, M03 timer active, M05 timer disabled and PID stable.
.venv/bin/python -B -m \
  scripts.m05_strategy_a_priority_preflight_readonly >/dev/null ||
  fail ORIGINAL_M05_PREFLIGHT_NOT_READY
systemctl --user is-enabled --quiet "$UNIT" ||
  fail ORIGINAL_M05_NOT_ENABLED_ON_BOOT
systemctl --user is-active --quiet "$M03" ||
  fail ORIGINAL_M03_TIMER_NOT_ACTIVE
BEFORE_PID="$(systemctl --user show "$UNIT" -p MainPID --value)"
[[ "$BEFORE_PID" =~ ^[1-9][0-9]{0,9}$ ]] ||
  fail ORIGINAL_M05_PID_UNVERIFIED
systemctl --user show "$UNIT" -p ExecStart --value |
  grep -Fq 'scripts/m05_telegram_service_tick_entry.py' ||
  fail ORIGINAL_M05_ENTRYPOINT_MISMATCH
AGE="$(ps -o etimes= -p "$BEFORE_PID" | tr -d ' ')"
[[ "$AGE" =~ ^[0-9]+$ && "$AGE" -ge 90 ]] ||
  fail ORIGINAL_M05_TOO_RECENTLY_STARTED

# Serialise this owner cutover; do not create another Telegram consumer.
LOCK_DIR="$HOME/.local/state/indodax-ai-bot-v2"
[[ -d "$LOCK_DIR" && ! -L "$LOCK_DIR" ]] || fail LOCK_DIRECTORY_MISSING
[[ "$(stat -c %u "$LOCK_DIR")" == "$(id -u)" ]] ||
  fail LOCK_DIRECTORY_OWNER_MISMATCH
exec 9>"$LOCK_DIR/m05-strategy-a-priority-once.lock"
flock -n 9 || fail ANOTHER_M05_CUTOVER_IN_PROGRESS
[[ "$BEFORE_PID" == "$(systemctl --user show "$UNIT" -p MainPID --value)" ]] ||
  fail M05_PID_CHANGED_DURING_PREFLIGHT

echo M05_PRIORITY_PREFLIGHT_PASSED
echo "M05_SOURCE=$(git rev-parse HEAD)"
echo "M05_PID_BEFORE=$BEFORE_PID"

# The ONLY service configuration mutation: one distinct optional drop-in.
install -m 0644 "$SRC" "$DROP" || fail DROPIN_INSTALL_FAILED
CUTOVER=YES
systemctl --user daemon-reload
systemctl --user show "$UNIT" -p DropInPaths --value |
  grep -Fq "$DROP" || fail PRIORITY_DROPIN_NOT_REGISTERED

RESTART_ATTEMPTED=YES
systemctl --user restart "$UNIT"
systemctl --user is-active --quiet "$UNIT" ||
  fail M05_FAILED_AFTER_PRIORITY_RESTART
NEW_PID="$(systemctl --user show "$UNIT" -p MainPID --value)"
[[ "$NEW_PID" =~ ^[1-9][0-9]{0,9}$ && "$NEW_PID" != "$BEFORE_PID" ]] ||
  fail NEW_M05_PID_NOT_VERIFIED

# Post-start allowlisted *process* flag check, never print /proc env.
M05_CHECK_PID="$NEW_PID" .venv/bin/python -B - <<'PY'
import os
from pathlib import Path
from scripts.m05_strategy_a_priority_preflight_readonly import (
    REQUIRED_FLAGS, PRIORITY_FLAG, summarize_allowlisted_process_flags)
pid = os.environ["M05_CHECK_PID"]
try:
    flags = summarize_allowlisted_process_flags(
        (Path("/proc") / pid / "environ").read_bytes())
    safe = all(flags.get(k) == v for k, v in REQUIRED_FLAGS.items())
    safe = safe and flags.get(PRIORITY_FLAG) == "YES"
except (OSError, ValueError):
    safe = False
print("M05_PRIORITY_NEW_PROCESS_GUARDS_OK=" + str(safe))
raise SystemExit(0 if safe else 3)
PY

# New-PID heartbeat must prove actual radar callback. No owner ACK is
# fabricated when market conditions produce no eligible candidate.
HEARTBEAT_OK=NO
for n in $(seq 1 90); do
  [[ "$NEW_PID" == "$(systemctl --user show "$UNIT" -p MainPID --value)" ]] ||
    fail M05_PID_CHANGED_DURING_HEARTBEAT
  systemctl --user is-active --quiet "$UNIT" ||
    fail M05_SERVICE_STOPPED_DURING_HEARTBEAT
  systemctl --user is-active --quiet "$M03" ||
    fail ORIGINAL_M03_TIMER_STOPPED
  HB_CODE=0
  HB="$(journalctl --user -u "$UNIT" -o json -n 700 --no-pager |
    M05_CHECK_PID="$NEW_PID" .venv/bin/python -B \
      -m scripts.m05_strategy_a_priority_heartbeat_readonly)" ||
    HB_CODE=$?
  if [[ "$HB_CODE" == 0 && "$HB" == M05_PRIORITY_HEARTBEAT_READY ]]; then
    HEARTBEAT_OK=YES
    break
  fi
  [[ "$HB_CODE" == 2 && "$HB" == M05_PRIORITY_HEARTBEAT_WAITING ]] ||
    fail PRIORITY_HEARTBEAT_UNSAFE_OR_UNAVAILABLE
  sleep 2
done
[[ "$HEARTBEAT_OK" == YES ]] || fail PRIORITY_HEARTBEAT_NOT_CONFIRMED

[[ "$NEW_PID" == "$(systemctl --user show "$UNIT" -p MainPID --value)" ]] ||
  fail M05_PID_CHANGED_AFTER_HEARTBEAT
systemctl --user is-active --quiet "$M03" ||
  fail ORIGINAL_M03_TIMER_STOPPED
[[ -z "$(git status --porcelain)" ]] ||
  fail SOURCE_CHANGED_DURING_ROLLOUT

SUCCESS=YES
echo M05_STRATEGY_A_PRIORITY_RUNTIME_ENABLED
echo M05_ORIGINAL_RECEIVER_ACTIVE
echo M03_SCANNER_TIMER_ACTIVE
echo M05_PRIORITY_NEW_PROCESS_GUARDS_OK
echo M05_PRIORITY_HEARTBEAT_READY
echo NO_LIVE_TRADING_NO_ORDERS_FROM_ROLLOUT
echo "M05_PID_AFTER=$NEW_PID"
echo OWNER_PRIVATE_NOTICE_REQUIRES_SEPARATE_ACTUAL_SEND_ACK_EVIDENCE
