#!/usr/bin/env bash
# Operator-invoked one-shot capture of a NEW original M04 WATCH, non-live.
# Writes only one private research JSON file for a valid current WATCH.
# No service changes, background worker, exchange calls, Telegram, AI or order.
set -euo pipefail

cd "$HOME/indodax-ai-bot-v2"
export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN
umask 077

DIR="$PWD/runtime/m04-watch-cohorts"
if [[ -L "$DIR" ]]; then
  echo "PRIVATE_COHORT_DIRECTORY_SYMLINK_REJECTED" >&2
  exit 2
fi
mkdir -p -m 0700 -- "$DIR"
chmod 0700 -- "$DIR"

TMP="$(mktemp "$DIR/.watch-pending.XXXXXXXX")"
cleanup() { rm -f -- "$TMP"; }
trap cleanup EXIT

if .venv/bin/python -B -m scripts.m04_ws3_watch_channel_coverage_readonly \
   --source-db "$PWD/runtime/decisions.sqlite3" \
   --capture-current > "$TMP"; then
  # The operator script produced a valid CURRENT M04 report by its own
  # stage/status contract. Its raw bytes remain private on disk only.
  .venv/bin/python -B - "$TMP" <<'PY'
import json
import sys
from pathlib import Path

r = json.loads(Path(sys.argv[1]).read_text())
if (r.get("status") != "M04_WATCH_TRADE_CHANNEL_COVERAGE_RESEARCH_ONLY"
        or r.get("capture_mode") != "CURRENT_OPERATOR_ONE_SHOT"
        or r.get("execution_allowed") is not False
        or r.get("pair_specific_ack_verified") is not False
        or r.get("candidate_count") not in (1, 2)):
    raise SystemExit("CURRENT_WATCH_CAPTURE_CONTRACT_INVALID")
PY
  DEST="$DIR/watch-$(date -u +%Y%m%dT%H%M%SZ)-$(basename "$TMP").json"
  if [[ -e "$DEST" || -L "$DEST" ]]; then
    echo "EVIDENCE_DESTINATION_ALREADY_EXISTS" >&2
    exit 2
  fi
  mv -- "$TMP" "$DEST"
  chmod 0600 -- "$DEST"
  trap - EXIT
  echo "CURRENT_WATCH_CAPTURE_SAVED: $DEST"
  echo "SOURCE_COVERAGE_UNVERIFIED; PAIR_ACK_UNVERIFIED; G1_UNVERIFIED"
  echo "NO_TRADE_OR_SERVICE_ACTION"
else
  echo "NO_VALID_CURRENT_M04_WATCH_CAPTURE; NO_EVIDENCE_FILE_SAVED"
  exit 0
fi
