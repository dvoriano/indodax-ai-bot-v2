#!/usr/bin/env bash
# Stage M10 operator-gated BTC PUBLIC operations on the EXISTING V2 service.
# No systemctl start/enable, no new writer, no GO file created.
# Usage: bash deploy/stage_m10_btc_disabled.sh <exact-tested-40-hex-commit>
set -euo pipefail
umask 077

if (( $# != 1 )) || [[ ! "$1" =~ ^[0-9a-f]{40}$ ]]; then
  echo "EXACT_TESTED_M10_SHA_REQUIRED"
  exit 2
fi
PIN="$1"
ROOT="$HOME/indodax-ai-bot-v2"
RELEASE="$HOME/indodax-ai-bot-v2-m10-ops"
BRANCH="feat/m10-btc-capped-ops-20261031"
SERVICE="indodax-v2-public-ws-btc.service"
TIMER="indodax-v2-public-ws-btc.timer"
UNIT="$HOME/.config/systemd/user/$SERVICE"
DROPIN_DIR="$HOME/.config/systemd/user/$SERVICE.d"
DROPIN="$DROPIN_DIR/90-capped-ops.conf"
TIMER_INSTALLED="$HOME/.config/systemd/user/$TIMER"

RELEASE_CREATED=0
DROPIN_CREATED=0
TIMER_CREATED=0
ACCEPTED=0

cleanup() {
  rc=$?
  trap - EXIT
  if (( ACCEPTED == 0 )); then
    if (( TIMER_CREATED )); then rm -f "$TIMER_INSTALLED"; fi
    if (( DROPIN_CREATED )); then
      rm -f "$DROPIN"
      rmdir "$DROPIN_DIR" 2>/dev/null || true
    fi
    if (( DROPIN_CREATED || TIMER_CREATED )); then
      systemctl --user daemon-reload || rc=1
    fi
    if (( RELEASE_CREATED )); then
      git -C "$ROOT" worktree remove --force "$RELEASE" || rc=1
    fi
    echo "M10_SOURCE_STAGING_NOT_COMMITTED"
  fi
  exit "$rc"
}
trap cleanup EXIT

echo "===== 1. NONACTIVE EXISTING SERVICE ====="
test -d "$ROOT/.git"
test -x "$ROOT/.venv/bin/python"
test -f "$UNIT"
test ! -L "$UNIT"
test ! -e "$RELEASE"
test ! -e "$DROPIN_DIR"
test ! -e "$TIMER_INSTALLED"
test ! -e "$ROOT/runtime/btc_public_ops.go.json"
test ! -e "$ROOT/runtime/btc_public_ops.admission.json"
test ! -e "$ROOT/runtime/btc_public_ops.hold.json"
cmp -s "$ROOT/deploy/$SERVICE" "$UNIT"
test "$(systemctl --user show "$SERVICE" -p MainPID --value)" = 0
STATE="$(systemctl --user show "$SERVICE" -p ActiveState --value)"
[[ "$STATE" == inactive || "$STATE" == failed ]]
test "$(systemctl --user show "$SERVICE" -p Restart --value)" = on-success
test -z "$(systemctl --user show "$SERVICE" -p DropInPaths --value)"
test "$(systemctl --user is-enabled indodax-v2-m03-mainpath.timer)" = disabled
test "$(systemctl --user is-active indodax-v2-m05-telegram.timer)" = active

echo "===== 2. PIN EXACT TESTED M10 SOURCE ====="
GIT_TERMINAL_PROMPT=0 timeout 30s \
  git -C "$ROOT" fetch --no-tags origin "$BRANCH"
test "$(git -C "$ROOT" rev-parse FETCH_HEAD)" = "$PIN"
git -C "$ROOT" worktree add --detach "$RELEASE" "$PIN"
RELEASE_CREATED=1
test -z "$(git -C "$RELEASE" status --porcelain)"
test -f "$RELEASE/indodax_ai_bot/btc_ops_gate.py"
test -f "$RELEASE/deploy/indodax-v2-public-ws-btc.capped-dropin.conf"
test -f "$RELEASE/deploy/$TIMER"

echo "===== 3. SOURCE CONFIG SCOPE ====="
test "$(git -C "$RELEASE" rev-parse HEAD)" = "$PIN"
test "$(systemctl --user show "$SERVICE" -p MainPID --value)" = 0
read -r -p "Ketik STAGE_M10_NONACTIVE (tidak ada service start): " CONFIRM </dev/tty
if [[ "$CONFIRM" != STAGE_M10_NONACTIVE ]]; then
  echo "STAGING_CANCELLED"
  exit 0
fi

echo "===== 4. STAGE OPERATOR GATE AND TIMER DISABLED ====="
mkdir -m 700 "$DROPIN_DIR"
cp "$RELEASE/deploy/indodax-v2-public-ws-btc.capped-dropin.conf" "$DROPIN"
DROPIN_CREATED=1
chmod 600 "$DROPIN"
cp "$RELEASE/deploy/$TIMER" "$TIMER_INSTALLED"
TIMER_CREATED=1
chmod 600 "$TIMER_INSTALLED"
systemctl --user daemon-reload

echo "===== 5. FAIL CLOSED / NO GO ====="
test "$(systemctl --user show "$SERVICE" -p Restart --value)" = no
test "$(systemctl --user show "$SERVICE" -p MainPID --value)" = 0
test "$(systemctl --user show "$SERVICE" -p DropInPaths --value)" = "$DROPIN"
test "$(systemctl --user is-active "$TIMER" || true)" = inactive
test "$(systemctl --user is-enabled "$TIMER" || true)" = disabled
test "$(systemctl --user is-enabled indodax-v2-m03-mainpath.timer)" = disabled
test "$(systemctl --user is-active indodax-v2-m05-telegram.timer)" = active
cmp -s "$RELEASE/deploy/indodax-v2-public-ws-btc.capped-dropin.conf" "$DROPIN"
cmp -s "$RELEASE/deploy/$TIMER" "$TIMER_INSTALLED"
cmp -s "$ROOT/deploy/$SERVICE" "$UNIT"

GATE_OUT="$(mktemp -p /tmp m10-disabled-XXXXXX)"
trap 'rm -f "$GATE_OUT"; cleanup' EXIT
if "$ROOT/.venv/bin/python" -B \
    "$RELEASE/indodax_ai_bot/btc_ops_gate.py" check > "$GATE_OUT" 2>&1; then
  echo "UNEXPECTED_GO_FOR_BTC_COLLECTOR"
  exit 1
fi
grep -Fxq "REQUIRED_PRIVATE_FILE_MISSING" "$GATE_OUT"
rm -f "$GATE_OUT"
trap cleanup EXIT

ACCEPTED=1
echo "M10_ORIGINAL_BTC_CAPPED_SOURCE_STAGED_DISABLED_PASS"
echo "M10_RELEASE_SHA=$PIN"
echo "BTC_ORIGINAL_EXECSTART_UNCHANGED"
echo "BTC_TIMER_DISABLED"
echo "BTC_SERVICE_INACTIVE"
echo "OWNER_GO_ABSENT"
echo "M03_TIMER_DISABLED"
echo "M05_ACTIVE"
echo "TRADING_DISABLED"
