#!/usr/bin/env bash
# M04-Q scoped VPS acceptance: 6 new + 8 directly affected tests, one PUBLIC G1 probe.
# Run from a detached V2 checkout/worktree. Never start/stop the collector or trade.
set -Eeuo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PY="${M04Q_PYTHON:-$HOME/indodax-ai-bot-v2/.venv/bin/python}"
WS="${M04Q_WS_DB:-$HOME/indodax-ai-bot-v2/runtime/public_ws_btc.sqlite3}"
SERVICE='indodax-v2-public-ws-btc.service'

if [[ ! -x "$PY" || ! -r "$WS" || ! -f "$ROOT/tests/test_m04q_ws_gap_minute_source.py" ]]; then
  echo 'M04Q_PRECHECK_FAILED: missing Python venv, readable WS DB, or M04-Q tests' >&2
  exit 2
fi

export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN
export PYTHONDONTWRITEBYTECODE=1

TMP="$(mktemp -d "${TMPDIR:-/tmp}/indodax-m04q.XXXXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT
cd "$ROOT"

echo "M04Q_CODE_SHA=$(git rev-parse HEAD)"
echo "M04Q_WS_DB=$WS"
echo '===== COLLECTOR STATE BEFORE (READ ONLY) ====='
systemctl --user show "$SERVICE" -p ActiveState -p SubState -p MainPID -p NRestarts -p Result --no-pager 2>&1 || true

echo '===== NEW M04-Q TESTS: EXPECT 6 ====='
"$PY" -B -m unittest discover -s tests -p 'test_m04q_ws_gap_minute_source.py' -v

echo '===== AFFECTED M04-H TESTS: EXPECT 8 ====='
"$PY" -B -m unittest discover -s tests -p 'test_m04h_closed_minute_join.py' -v

echo '===== ONE REAL PUBLIC BULK G1 OBSERVATION AGAINST PERSISTENT V2 WS SQLITE ====='
"$PY" -B -m indodax_ai_bot \
  --bulk-deep-once --ws-db "$WS" --db "$TMP/decisions.sqlite3" \
  > "$TMP/result.json"

"$PY" -B - "$TMP/result.json" <<'PY'
import json, sys
from pathlib import Path
result = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
bulk = result.get('public_bulk') or {}
ws = bulk.get('btc_ws_closed_minute_sample') or {}
compare = bulk.get('btc_rest_ws_observability') or {}
roles = (result.get('ai_shadow') or {}).get('completed_roles')
fields = {
    'final_decision': result.get('final_decision'),
    'execution_allowed': result.get('execution_allowed'),
    'live_trading_authorized': result.get('live_trading_authorized'),
    'g1_verified': bulk.get('g1_verified'),
    'ai_completed_roles': roles,
    'btc_ws_closed_minute_sample': {
        k: ws.get(k) for k in (
            'status', 'reason', 'closed_1m_epoch', 'trade_rows_visible_in_minute',
            'unrecoverable_gap_rebaseline_count', 'minute_segment_continuity_status',
            'minute_trade_coverage_complete', 'g1_receipt_issuance_allowed',
            'g2_provider_call_allowed', 'execution_allowed'
        )
    },
    'btc_rest_ws_observability': {
        k: compare.get(k) for k in (
            'status', 'reason', 'public_ws_gap_rejection_reason',
            'public_ws_gap_in_minute'
        )
    },
}
print(json.dumps(fields, indent=2, sort_keys=True))
if not isinstance(result.get('public_bulk'), dict) or not isinstance(ws, dict) or not ws:
    raise SystemExit('M04Q_FAIL: public bulk or real WS diagnostic absent')
if (result.get('final_decision') != 'REJECT'
        or result.get('execution_allowed') is not False
        or result.get('live_trading_authorized') is not False
        or bulk.get('g1_verified') is not False
        or roles != []
        or ws.get('g1_receipt_issuance_allowed') is not False
        or ws.get('g2_provider_call_allowed') is not False
        or ws.get('execution_allowed') is not False):
    raise SystemExit('M04Q_FAIL: unexpected source/AI/order authority')
print('M04Q_PUBLIC_G1_SAFE_REJECT_OBSERVED')
PY

echo '===== COLLECTOR STATE AFTER (READ ONLY) ====='
systemctl --user show "$SERVICE" -p ActiveState -p SubState -p MainPID -p NRestarts -p Result --no-pager 2>&1 || true
echo 'M04Q_SCOPED_CHECKS_COMPLETE_VPS_OUTPUT_REQUIRED_FOR_ACCEPTANCE'
