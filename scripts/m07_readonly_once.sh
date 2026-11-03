#!/usr/bin/env bash
# Owner-triggered, single bounded original V2 M03->M06 View Only->M07 check.
# Not a daemon, scheduler, paper/live executor, or order authorization.
set -euo pipefail
umask 077

: "${HOME:?HOME required}"
ROOT="$(cd -- "$HOME/indodax-ai-bot-v2" && pwd -P)"
SCRIPT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ "$ROOT" != "$SCRIPT_ROOT" ]]; then
  printf 'M07_ABORT_NOT_ORIGINAL_V2_ROOT\n' >&2
  exit 3
fi
cd -- "$ROOT"

# Do not silently run an older or a locally modified code path.
EXPECTED_FIX="b9c2ac4ceda968e51665e776c00109335be67391"
if [[ "$(git symbolic-ref --quiet --short HEAD)" != "main" ]] ||
   ! git merge-base --is-ancestor "$EXPECTED_FIX" HEAD ||
   [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  printf 'M07_ABORT_SOURCE_NOT_MAIN_OR_PR76_MISSING_OR_MODIFIED\n' >&2
  exit 3
fi

PYTHON="$ROOT/.venv/bin/python"
WS="$ROOT/runtime/public_ws_btc.sqlite3"
DB="$ROOT/runtime/decisions.sqlite3"
if [[ ! -x "$PYTHON" || ! -s "$WS" || ! -d "$ROOT/runtime" ]]; then
  printf 'M07_ABORT_V2_PYTHON_OR_EXISTING_BTC_WS_MISSING\n' >&2
  exit 3
fi

# Owner's one-time 3 Nov 2026 scenario: MAIN source is current free IDR.
# Do not infer a historical special-token sale hold. If a NEW real FLOKI/
# PEPE sale or unsettled commitment occurs, do NOT reuse these zero holds.
BUDGET="$(mktemp "$ROOT/runtime/.m07-once-funding.XXXXXXXX")"
trap 'rm -f -- "$BUDGET"' EXIT
cat > "$BUDGET" <<'JSON'
{
  "requested_ticket_ceiling_idr": "100000",
  "new_floki_sell_rebuy_hold_idr": "0",
  "new_pepe_sell_rebuy_hold_idr": "0",
  "commitments_not_locked_idr": "0",
  "max_spread_bps": "150"
}
JSON

printf 'M07_READONLY_INSTALLED_SHA=%s\n' "$(git rev-parse HEAD)"
printf 'M07_READONLY_SCOPE=M03_PUBLIC_20_PAIRS_PLUS_M06_TWO_SIGNED_VIEW_ONLY_GETS_NO_ORDERS\n'

# Minimal environment. The original CLI discovers ONLY existing V2 View
# Only credentials; never put credentials, raw payloads or tokens in output.
# PUBLIC budget remains enforced by the original shared REST admission.
env -i \
  HOME="$HOME" \
  PATH="/usr/bin:/bin" \
  PYTHONDONTWRITEBYTECODE=1 \
  INDODAX_TRADING_ENABLED=NO \
  INDODAX_ALLOW_LIVE_EXECUTION=NO \
  INDODAX_EXECUTION_MODE=DRY_RUN \
  timeout 180s \
  "$PYTHON" -B -m indodax_ai_bot \
    --m07-main-readonly-cycle-once \
    --m07-runtime-funding-file "$BUDGET" \
    --ws-db "$WS" \
    --db "$DB" \
    --bulk-min-volume-idr 150000000 \
    --bulk-deep-limit 20
