#!/usr/bin/env bash
# Original V2 M03 opt-in 72h research source archive, WITHOUT a service restart.
# Requires explicit owner GO; normal existing M03 timer loads on its next tick.
set -Eeuo pipefail
umask 077

ROOT="$HOME/indodax-ai-bot-v2"
UNIT="indodax-v2-m03-mainpath.service"
TIMER="indodax-v2-m03-mainpath.timer"
M05="indodax-v2-m05-telegram.service"
DROP_DIR="$HOME/.config/systemd/user/$UNIT.d"
SRC="systemd/user/$UNIT.d/zzz-micro-72h-evidence.conf"
DROP="$DROP_DIR/zzz-micro-72h-evidence.conf"
PRIOR="0d7af2d19bc1a1d37cb0dda70bc354639f224612"
CUTOVER=NO
SUCCESS=NO

fail() { echo "M03_MICRO_72H_OPTIN_BLOCKED:$1" >&2; exit 3; }
rollback() {
  code=$?
  trap - EXIT
  if [[ "$CUTOVER" == YES && "$SUCCESS" != YES ]]; then
    echo "M03_MICRO_72H_ROLLBACK_STARTED" >&2
    rm -f -- "$DROP" || echo "M03_MICRO_72H_ROLLBACK_DROP_REMOVE_FAILED" >&2
    systemctl --user daemon-reload ||
      echo "M03_MICRO_72H_ROLLBACK_DAEMON_RELOAD_FAILED" >&2
    echo "M03_MICRO_72H_ROLLBACK_CONFIG_RESTORED_ATTEMPTED" >&2
    # NEVER stop/restart M03 or M05. An already-started natural M03 tick
    # cannot be undone by removing the flag; no false history claims.
  fi
  exit "$code"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "${M03_MICRO_72H_OWNER_ENABLE_GO:-NO}" == YES ]] ||
  fail EXPLICIT_OWNER_ENABLE_GO_REQUIRED
export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN

cd "$ROOT" || fail ORIGINAL_SOURCE_MISSING
[[ "$(git branch --show-current)" == main ]] || fail NOT_ORIGINAL_MAIN
[[ -z "$(git status --porcelain)" ]] || fail DIRTY_ORIGINAL_SOURCE
git merge-base --is-ancestor "$PRIOR" HEAD ||
  fail REVIEWED_MICRO_SOURCE_NOT_IN_MAIN

[[ -f "$SRC" && ! -L "$SRC" ]] || fail REVIEWED_DROPIN_MISSING
cmp -s "$SRC" <(printf '%s\n' '[Service]' \
  'Environment=M03_WS3_MICRO_72H_ARCHIVE_GO=YES') ||
  fail UNEXPECTED_FLAG_CONTENT
[[ -d "$DROP_DIR" && ! -L "$DROP_DIR" ]] ||
  fail ORIGINAL_M03_DROPIN_DIRECTORY_UNTRUSTED
[[ "$(stat -c %u "$DROP_DIR")" == "$(id -u)" ]] ||
  fail ORIGINAL_M03_DROPIN_OWNER_INVALID
MODE="$(stat -c %a "$DROP_DIR")"
[[ "$MODE" == 700 || "$MODE" == 755 ]] ||
  fail ORIGINAL_M03_DROPIN_MODE_UNEXPECTED
[[ ! -e "$DROP" && ! -L "$DROP" ]] ||
  fail ORIGINAL_M03_OPTIN_DROPIN_ALREADY_PRESENT

systemctl --user is-active --quiet "$TIMER" ||
  fail ORIGINAL_M03_TIMER_NOT_ACTIVE
systemctl --user is-active --quiet "$M05" ||
  fail ORIGINAL_M05_NOT_ACTIVE
M05_PID="$(systemctl --user show "$M05" -p MainPID --value)"
[[ "$M05_PID" =~ ^[1-9][0-9]{0,9}$ ]] ||
  fail ORIGINAL_M05_PID_INVALID

.venv/bin/python -B -m \
  scripts.m03_micro_72h_owner_preflight_readonly >/dev/null ||
  fail REAL_ORIGINAL_M03_PREFLIGHT_BLOCKED

echo "M03_MICRO_72H_REAL_PREFLIGHT_PASSED"
echo "M03_MICRO_72H_SOURCE=$(git rev-parse HEAD)"
echo "M05_ORIGINAL_PID_BEFORE=$M05_PID"

# Only change: one optional isolated research flag in existing original
# M03 oneshot drop-in directory. No unit start, stop, timer edit or restart.
CUTOVER=YES
install -m 0644 "$SRC" "$DROP" ||
  fail OPTIONAL_M03_DROPIN_INSTALL_FAILED
systemctl --user daemon-reload
# Re-evaluate original effective Environment AND private EnvironmentFiles.
.venv/bin/python -B -m \
  scripts.m03_micro_72h_owner_preflight_readonly \
  --expect-enabled ||
  fail POSTRELOAD_ORIGINAL_M03_FLAGS_NOT_VERIFIED

systemctl --user is-active --quiet "$TIMER" ||
  fail ORIGINAL_M03_TIMER_STOPPED
systemctl --user is-active --quiet "$M05" ||
  fail ORIGINAL_M05_STOPPED
[[ "$M05_PID" == "$(systemctl --user show "$M05" -p MainPID --value)" ]] ||
  fail M05_PROCESS_CHANGED_DURING_OPTIN
[[ -z "$(git status --porcelain)" ]] ||
  fail GIT_SOURCE_CHANGED_DURING_OPTIN

SUCCESS=YES
echo "M03_MICRO_72H_CONFIG_ENABLED_NEXT_NATURAL_TICK"
echo "M03_TIMER_UNCHANGED_ACTIVE"
echo "M05_ORIGINAL_PROCESS_UNCHANGED"
echo "NO_RESTART_NO_TELEGRAM_SEND_NO_ORDERS"
echo "M03_MICRO_72H_ARCHIVE_ROWS_NOT_YET_PROVEN"
