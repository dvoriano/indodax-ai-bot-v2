# M10-06 | Original V2 operations alert snapshot (READ ONLY, no 24h proof)

**Code source:** [PR#119](https://github.com/dvoriano/indodax-ai-bot-v2/pull/119), merged source SHA
`9be989b5a1854e18b55f1b2d9205e0eb1e16c4a0`. GitHub [M01 CI
41755888798](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41755888798)
**1135/1135 tests PASS + safe CLI**, M07 targeted
[41755888604](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41755888604)
GREEN. Twelve new tests cover REJECT 10/5/4, missing/stale/future
batch status, untrusted fields, missing BTC service, M05 timer off,
M03 timer unexpected enable, invalid source paths/permissions and output
redaction. **No owner VPS execution for this new module has been proved.**

The module `indodax_ai_bot.m10_ops_health_readonly` deliberately uses
the existing original fixed-five-user-unit status reader
`m10_v2_units_readonly.inspect_named_v2_units()` and the **original**
`public_ws_supervisor.py` status JSON keys. It does **not** read
the external script token in the BTC WS `ExecCondition`, access other
projects or services, call Telegram, create a watchdog, issue public/private
requests, modify SQLite, change systemd, or trade.

## Owner VPS, ONE pinned source, READ ONLY

```bash
(
  set -euo pipefail
  umask 077

  ROOT="$HOME/indodax-ai-bot-v2"
  OP_HOME="$HOME"
  SHA="9be989b5a1854e18b55f1b2d9205e0eb1e16c4a0"
  TMP="$(mktemp -d /tmp/v2-m10-ops-ro-XXXXXX)"
  WT="$TMP/code"

  cleanup() {
    git -C "$ROOT" worktree remove --force "$WT" \
      >/dev/null 2>&1 || true
    rm -rf "$TMP"
  }
  trap cleanup EXIT

  echo "===== VERIFY PINNED V2 OPS SOURCE ====="
  GIT_TERMINAL_PROMPT=0 timeout 30s \
    git -C "$ROOT" fetch --no-tags origin main
  git -C "$ROOT" merge-base --is-ancestor "$SHA" FETCH_HEAD
  git -C "$ROOT" worktree add --detach "$WT" "$SHA"
  test "$(git -C "$WT" rev-parse HEAD)" = "$SHA"

  echo "M10_OPS_SNAPSHOT_SOURCE_SHA=$SHA"
  echo "===== V2 OPS READ-ONLY SNAPSHOT ====="
  (
    cd "$WT"
    timeout 30s env -i \
      HOME="$OP_HOME" \
      PATH="/usr/bin:/bin" \
      XDG_RUNTIME_DIR="/run/user/$(id -u)" \
      PYTHONPATH="$WT" \
      PYTHONDONTWRITEBYTECODE=1 \
      INDODAX_TRADING_ENABLED=NO \
      INDODAX_ALLOW_LIVE_EXECUTION=NO \
      INDODAX_EXECUTION_MODE=DRY_RUN \
      "$ROOT/.venv/bin/python" -B \
      -m indodax_ai_bot.m10_ops_health_readonly
  )
  echo "M10_OPS_READONLY_SNAPSHOT_COMPLETE"
)
```

The original active V2 checkout and systemd units are **not** updated
or started. This command calls only `systemctl --user show` for the
fixed five V2 unit names, and reads the one original V2 BTC WS last-batch
sidecar (owner-only, bounded and no-follow). No journal history or
other-host egress metrics are read. A systemd user-bus failure yields
`*_STATUS_UNVERIFIED` warnings. The pinned source SHA verifies the
**diagnostic code**, not any currently running process.

## How to read JSON and what remains unverified

`alert_codes` contains sanitized warnings only: `BTC_WS_BATCH_REJECTED`,
`BTC_WS_BATCH_STALE`, `BTC_WS_BATCH_INCOMPLETE_OR_INCONSISTENT`,
`BTC_WS_UNIT_NOT_ACTIVE`, `M05_TIMER_NOT_ACTIVE`,
`M03_TIMER_NOT_CONFIRMED_DISABLED`, or an UNKNOWN/UNVERIFIED alert
when files/unit metadata cannot be trusted. Empty warnings are **not**
continuous uptime proof. `last_batch_status` and
`last_batch_age_seconds` concern the **last persisted completed batch**;
a DISCONNECTED stream at batch end is expected. The known historical
V2 WS status `REJECT` 10 requested, 5 attempted, 4 safe, empty/closed
stream and the installed external-token ExecCondition guard must not be
reinterpreted as a fresh event or a permitted restart.

All of the following are forcibly **false**:
`continuous_24h_heartbeat_verified`, `alerts_delivered_to_owner_verified`,
`actual_runtime_source_sha_verified`,
`systemd_guard_root_cause_verified`, `service_started_or_restarted`,
`service_restart_authorized`, `m03_timer_go_authorized`,
`m10_06_full_acceptance`, `live_trading_authorized`.

**Roadmap gate:** M10-06 has source-only offline alert code and GitHub CI,
but **no original 24h watchdog/mainpath deployment**, no uptime or
real Telegram alert receipt, and no authorized BTC collector restart.
M10-05 independently verified all-NAT/egress-IP REST consumers and
rolling 60s request peak also remain open. M10 acceptance stays 4/7.
Only rerun after relevant new evidence or source change; do not repeat
accepted 1135 CI / 25/25 and 31/31 VPS tests. Do not open the non-V2
helper or change BTC WS/M03 services, private API, AI providers or orders.
