# M10-06 | Original V2 operations alert snapshot (READ ONLY, no 24h proof)

**Code source:** [PR#119](https://github.com/dvoriano/indodax-ai-bot-v2/pull/119), merged source SHA
`9be989b5a1854e18b55f1b2d9205e0eb1e16c4a0`. GitHub [M01 CI
41755888798](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41755888798)
**1135/1135 tests PASS + safe CLI**, M07 targeted
[41755888604](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41755888604)
GREEN. Twelve new tests cover REJECT 10/5/4, missing/stale/future
batch status, untrusted fields, missing BTC service, M05 timer off,
M03 timer unexpected enable, invalid source paths/permissions and output
redaction. **Owner VPS completed this specific module 4 Nov 2026**, marker `M10_OPS_READONLY_SNAPSHOT_COMPLETE` on exact `9be989b` source. Do NOT rerun the already accepted snapshot.

The module `indodax_ai_bot.m10_ops_health_readonly` deliberately uses
the existing original fixed-five-user-unit status reader
`m10_v2_units_readonly.inspect_named_v2_units()` and the **original**
`public_ws_supervisor.py` status JSON keys. It does **not** read
the external script token in the BTC WS `ExecCondition`, access other
projects or services, call Telegram, create a watchdog, issue public/private
requests, modify SQLite, change systemd, or trade.


## Owner VPS read-only original V2 alert observation (COMPLETED 4 November)

Owner's verified pinned source `9be989b5a1854e18b55f1b2d9205e0eb1e16c4a0`
produced `M10_OPS_READONLY_SNAPSHOT_COMPLETE`:

- `alert_codes`: `BTC_WS_BATCH_INCOMPLETE_OR_INCONSISTENT`, `BTC_WS_BATCH_REJECTED`, `BTC_WS_BATCH_STALE`, `BTC_WS_UNIT_NOT_ACTIVE`.
- Original last batch: `REJECT`, 10 requested, 5 attempted, 4 completed safe, `failure_code_observed=true`, `restart_only_after_clean_batch_observed=false`, `last_batch_age_seconds=291904` (snapshot age), stream disconnected.
- M03 timer disabled metadata `true`, M05 timer active metadata `true`, BTC WS unit active metadata `false`.
- `continuous_24h_heartbeat_verified=false`, `alerts_delivered_to_owner_verified=false`, `m10_06_full_acceptance=false`, `service_restart_authorized=false`, `live_trading_authorized=false`.

This is a genuine original V2 *local read-only* diagnostic, not a
new WS batch, a confirmed 24h uptime span, a Telegram delivery, or
permission to change the installed service/drop-in.

## M05 original `/ops` command, scoped OFFLINE integration (GitHub CI PASS)

[PR#121](https://github.com/dvoriano/indodax-ai-bot-v2/pull/121)
merged `323c309da5db442ab4c5fbd215b2415e3464a268` for the
original `OfflineTelegramOwnerInterface.handle_command` owner/private
`/ops`, with fixed/redacted V2 classifier message.
[Actions 41759139350](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41759139350):
**1143/1143 PASS + safe CLI**, M07 non-live GREEN.

[PR#122](https://github.com/dvoriano/indodax-ai-bot-v2/pull/122)
merged `5cbd4c5e67f272fa898939d3fa77e10ee059ba61`. Two more
tests call the **original** `TelegramOwnerPoller.poll_once`
with the original owner ledger and fake Bot API injected
`allow_network=False`, prove one owner-private reply and durable
cursor no-replay, and prove nonowners cannot query V2 unit status.
[Actions 41759421076](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41759421076):
**1145/1145 PASS** including safe CLI.

This is not the real installed M05 service, actual Bot API network, or
Telegram delivery proof. No source-only code is automatically deployed.
`/ops` is owner-requested and read-only, not an automatic push alert.

### Owner VPS V2-only targeted offline source verification (COMPLETED 4 Nov 2026)

```bash
(
  set -euo pipefail
  umask 077
  ROOT="$HOME/indodax-ai-bot-v2"
  SHA="5cbd4c5e67f272fa898939d3fa77e10ee059ba61"
  TMP="$(mktemp -d /tmp/v2-m10-owner-ops-XXXXXX)"
  WT="$TMP/code"

  cleanup() {
    git -C "$ROOT" worktree remove --force "$WT" \
      >/dev/null 2>&1 || true
    rm -rf "$TMP"
  }
  trap cleanup EXIT

  GIT_TERMINAL_PROMPT=0 timeout 30s \
    git -C "$ROOT" fetch --no-tags origin main
  git -C "$ROOT" merge-base --is-ancestor "$SHA" FETCH_HEAD
  git -C "$ROOT" worktree add --detach "$WT" "$SHA"
  test "$(git -C "$WT" rev-parse HEAD)" = "$SHA"
  echo "M10_OWNER_OPS_ORIGINAL_SOURCE_SHA=$SHA"
  (
    cd "$WT"
    timeout 90s env -i \
      HOME="$HOME" \
      PATH="/usr/bin:/bin" \
      PYTHONPATH="$WT" \
      PYTHONDONTWRITEBYTECODE=1 \
      INDODAX_TRADING_ENABLED=NO \
      INDODAX_ALLOW_LIVE_EXECUTION=NO \
      INDODAX_EXECUTION_MODE=DRY_RUN \
      "$ROOT/.venv/bin/python" -B -m unittest -v \
      tests.test_m10_06_owner_ops_original_m05
  )
  echo "M10_OWNER_OPS_ORIGINAL_OFFLINE_TEST_PASS"
)
```

**OWNER VPS EXECUTION OBSERVED, do not repeat:** owner fetched exact original V2 detached `5cbd4c5e67f272fa898939d3fa77e10ee059ba61`, printed `M10_OWNER_OPS_ORIGINAL_SOURCE_SHA=5cbd4c5e67f272fa898939d3fa77e10ee059ba61`, ran this single unittest module and received **`Ran 10 tests in 0.145s` / `OK`**, followed by `M10_OWNER_OPS_ORIGINAL_OFFLINE_TEST_PASS`. All ten were original offline owner command and Telegram fake transport tests, with no installed M05 service mutation, Bot API network, paid AI, private exchange calls, outside-V2 guard script or order. Original V2 scoped VPS checkout test evidence is now accepted; M10-06 checkout ✅. **Not** original active M05 runtime deployment, real Bot API send, automatic alert delivery, or continuous 24h acceptance. BTC WS service remains un-restarted and M03 timer remains disabled. Future operational deployment requires distinct explicit owner authorization and a V2-only rollback-safe release review, not more repetition of this test.

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
