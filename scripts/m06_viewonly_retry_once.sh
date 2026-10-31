#!/usr/bin/env bash
# Execute only from an exact pinned GitHub commit supplied by the owner.
set -euo pipefail
umask 077
ROOT="$HOME/indodax-ai-bot-v2"
BR="feat/m06-account-reconcile-hardlocked-20261031"
SHA="${1:?Required pinned SHA}"
PY="$ROOT/.venv/bin/python"
STATE="$HOME/.local/state/indodax-ai-bot-v2"
BUDGET="$STATE/m06-owner-viewonly-retry-20261031.sqlite3"

if [[ ! "$SHA" =~ ^[a-f0-9]{40}$ ]]; then
  echo "M06_RETRY_SHA_INVALID" >&2
  exit 2
fi
test -x "$PY"
test "$(git -C "$ROOT" rev-parse FETCH_HEAD)" = "$SHA"
test "$(git -C "$ROOT" rev-parse "refs/remotes/origin/$BR")" = "$SHA"
if [ -e "$BUDGET" ] || [ -L "$BUDGET" ]; then
  echo "M06_RETRY_ALREADY_ATTEMPTED_STOP" >&2
  exit 3
fi

TMP="$(mktemp -d /tmp/v2-m06-owner-retry-XXXXXX)"
WT="$TMP/source"
cleanup() {
  git -C "$ROOT" worktree remove --force "$WT" >/dev/null 2>&1 || true
  rm -rf -- "$TMP"
}
trap cleanup EXIT
git -C "$ROOT" worktree add --detach "$WT" "$SHA"
cd "$WT"

echo "M06_RETRY_TESTING_PINNED_V2"
env -i HOME="$HOME" PATH="/usr/bin:/bin" PYTHONDONTWRITEBYTECODE=1 \
  INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO \
  INDODAX_EXECUTION_MODE=DRY_RUN \
  timeout 90s "$PY" -B -m unittest -q \
  tests.test_m06_scoped_https tests.test_m06_owner_retry \
  tests.test_m06_original_private_go_mainpath
echo "M06_RETRY_OFFLINE_PASS"

install -d -m 0700 "$STATE"
if [ -e "$BUDGET" ] || [ -L "$BUDGET" ]; then
  echo "M06_RETRY_ALREADY_ATTEMPTED_STOP" >&2
  exit 3
fi

echo "===== MASUKKAN 2 NILAI SAJA ====="
echo "1. API Key View Only lalu ENTER"
echo "2. Secret Key View Only lalu ENTER"
echo "Karakter memang tidak tampil. Jangan paste kode."
env -i HOME="$HOME" PATH="/usr/bin:/bin" PYTHONDONTWRITEBYTECODE=1 \
  INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO \
  INDODAX_EXECUTION_MODE=DRY_RUN \
  M06_OWNER_ONE_SHOT_PRIVATE_GO=M06_PRIVATE_VIEW_ONLY_RETRY_20261031 \
  timeout 240s "$PY" -B -m indodax_ai_bot.m06_owner_retry
