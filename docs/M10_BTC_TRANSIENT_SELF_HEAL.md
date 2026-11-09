# M10 original BTC public transient self-healing, without a second worker

## Owner-approved scope

On 8 November 2026 the owner requested that transient BTC collector failures no longer leave the **original** 24/7 public scanner stalled indefinitely. This is **PUBLIC read-only** recovery only, not a relaxation of 10/10 source quality, trade evidence G1, private Indodax access, Sumopod paid AI billing, Telegram or BUY/SELL/CANCEL.

At owner VPS diagnosis BTC `PUBLIC_WS_OPERATOR_HOLD`, M03 `M03_STANDING_BTC_SOURCE_UNHEALTHY`, original M03 service FAILED, 0 original ticker pairs over 10 minutes, and M05 Telegram active but had 4 past transport-related restarts. **These error codes alone do NOT establish that the HOLD represents a transient transport fault.** The protected prior sidecar + failure code + gap ledger must be examined by the new safe read-only diagnostic first.

## Architecture

**No additional service, timer, scanner, or external polling.** The current original `indodax-v2-public-ws-btc.service` already runs `btc_ops_gate.py check` as its `ExecCondition`, and `btc_ops_gate.py exit-hook` as its `ExecStopPost`. This PR optionally lets that exact check perform one self-heal when all original credentials and evidence preconditions pass:

1. Owner must **explicitly stage** a fixed, 0600 local opt-in in `~/.config/indodax-ai-bot-v2/m10-btc-self-heal.json` via `scripts.m10_btc_self_heal_owner --owner-enable-standing-transient --owner-confirm OWNER_ENABLE_ORIGINAL_V2_PUBLIC_TRANSIENT_SELF_HEAL`. This script first validates original standing BTC PUBLIC-only GO, original single BTC user service and installed original capped drop-in, active existing BTC timer, and active original M05. Does **NOT** clear HOLD or start any unit.
2. The original gate checks for existing HOLD. With **no** owner opt-in it behaves exactly as before. With opt-in it invokes local `m10_btc_self_heal.maybe_auto_heal_original_gate`, not a second process or timer.
3. Accept **only** a private 0600 exactly canonical machine-placed `{"reason":"PUBLIC_WS_SERVICE_FAILURE_HOLD","scope":"BTC_PUBLIC_MONITOR_ONLY"}`. Any other/manual/unknown HOLD remains untouched. Require the original owner-signed standing PUBLIC-only GO (no expiry, original gap budget), original rejected batch status and matching persisted BTC WS gap ledger. Must be the original 10-session BTC batch with attempted 1-10, completed strictly below attempts and exact failed session count, `stream_status=DISCONNECTED`, source G1/G2/order false, and failure code one of only the **five typed original recv() connection-loss subtypes** under `WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_`: `CLOSED_OR_LOST`, `CONNECTION_RESET`, `CONNECTION_ABORTED`, `BROKEN_PIPE`, `EOF`. Generic `WS_SESSION_UNAVAILABLE`, TLS, protocol/payload problems, heartbeat gaps, journal errors and unknown exceptions **never** auto-heal.
4. **Revised 9 November 2026:** An old REJECT is historical evidence of a machine failure, **not** current market data. A narrowly typed machine HOLD and exact REJECT (after the standing PUBLIC GO was issued) may initiate **one new bounded PUBLIC-only reconnect batch even if the failed batch is older than 24 hours**. This prevents stale REJECT age alone from freezing monitoring after lengthy network loss or Indodax maintenance. Do **not** recycle old prices, market source, G1/AI output or orders. Never recover missing/unsafe source, different failure reason, unknown/manual HOLD, gap discrepancy/budget exceedance, malformed status, missing standing GO, or unauthorized G1/execution claim. There remain at most **2 distinct transient recovery attempts within rolling 24 hours**, with **1800 seconds between attempts**. Existing BTC start cooldown stays >=600s; the full 10/10 fresh BTC result is still mandatory before M03 resumes.
5. Typed connection losses during original WebSocket CONNECT and AUTH_RECEIVE and an exact HTTP 502/503/504 handshake (server/gateway unavailable) are also considered transient PUBLIC transport failures. HTTP 401/403 authentication refusal, HTTP 429 rate limiting, unknown, TLS, protocol/payload, and unverified failure codes remain blocked. No automatic paid requests, private API or trades occur. The same fail-stop HOLD is reinstated if the fresh batch fails again.\n6. Under a local exclusive file lock, stage a permanent 0600 attempt history and an **exact, one-hour expiry, source-SHA256-bound one-batch recovery receipt** before atomically archiving the original machine HOLD to a private backup. Crashes, source mutation and malformed state fail closed. No GO policy is rewritten or expanded. The existing BTC `_policy_checks` accepts the receipt only if opt-in and durable matching state still hold. It **never** calls the provider, creates G1 source, fabricates a clean batch or bypasses gap limits.
7. The original BTC timer alone attempts a new supervised batch, whose `ExecStopPost` again installs failure HOLD on exit if anything goes wrong. The original M03 standing gate **does not accept this recovery receipt** as fresh market evidence: M03 resumes only after the original BTC sidecar has an actual successful 10/10, `collector_failure_code=None`, matching gaps and a batch younger than one hour.
8. Once two failed attempts or an unsafe reason occur, **operator review remains mandatory**. This is a bounded self-heal canary, not a guarantee of perfect 24/7 uptime and not permission to restart the M05 Telegram bot. Existing PUBLIC trading OFF and owner-paid Sumopod one-shot authorization are independent.

## Safe owner acceptance, before enabling

After merging PR and syncing original clean `main`:

```bash
.venv/bin/python -B -m unittest -q tests.test_m10_btc_self_heal
.venv/bin/python -B -m scripts.m10_btc_self_heal_owner --diagnose-readonly
.venv/bin/python -B -m indodax_ai_bot.btc_ops_gate diagnose
systemctl --user show indodax-v2-public-ws-btc.service \
  -p ActiveState -p Result -p ExecMainStatus --no-pager
systemctl --user show indodax-v2-public-ws-btc.timer \
  -p ActiveState -p UnitFileState --no-pager
```

Diagnosis reports `transient_public_source_allowlisted` and `source_first_blocker` **without** reading or outputting raw private JSON, tokens or file paths.

If source preflight reports a known recoverable transient machine failure and the owner approves activation for that specific original machine, run exactly:

```bash
.venv/bin/python -B -m scripts.m10_btc_self_heal_owner \
  --owner-enable-standing-transient \
  --owner-confirm OWNER_ENABLE_ORIGINAL_V2_PUBLIC_TRANSIENT_SELF_HEAL
```

This **stages consent only**, no service start or HOLD removal. On the **next natural existing BTC timer cycle**, the original `btc_ops_gate.py check` may recover one eligible machine transient HOLD, then the original collector produces an actual fresh batch or returns to HOLD. If current HOLD is manual, predates standing GO, non-transport, or journal/status inconclusive, the process correctly stays blocked. An aged machine failure by itself does not block a bounded PUBLIC retry. Do not delete HOLD manually to coerce it.

**No unbounded auto-retry or restart cascade.** On fail, inspect `btc_ops_gate diagnose`, the read-only self-heal diagnosis and the original status. No `systemctl reset-failed` or M03/M05 restart is necessary to investigate.

## Operational rollback

Owner can revoke further automatic recovery at any time by moving the exact private consent file out of the canonical opt-in path using a private backup; do **not** delete original failed batches, HOLD, archived evidence, or self-heal attempt history. Removing opt-in makes standing REJECT receipt invalid and returns original fail-stop rules until a genuine subsequent 10/10 succeeds. If an original BTC service is currently running, let it finish safely; do not change units midbatch.

## Acceptance and roadmap

Source + CI is **not** proof of original owner VPS 24/7 continuity or a current eligible HOLD. Original M05 can remain active even while BTC/M03 halted. Require owner original source diagnosis, explicit opt-in, one naturally scheduled BTC 10/10 healthy batch with gaps matched, original M03 new fresh ticker samples and M05 no further unexpected restarts before declaring production restoration. No new master checklist boxes are ticked from this PR alone; current 52/98 DONE, 46 OPEN.
