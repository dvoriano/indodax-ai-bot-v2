# M10-05 | Verifikasi shared egress-IP tanpa menyentuh V1/Hermes

**Status 4 Nov 2026:** M10-05 **PARTIAL**, latest V2 source and VPS scoped OFFLINE tests PASS; independent egress-IP inventory/rolling-60s proof remains OPEN. Original V2 scanner M03 remains staged/disabled, orders OFF, paid AI OFF. Do not turn on the timer or make private API/trading calls using this page.

## Dua bukti berbeda, jangan dicampur

1. **V2 LOCAL SHARED QUOTA**: original `IndodaxPublicMarket._get` always calls `SharedPublicRestBudget.reserve` before REAL public REST network. PR#100 main SHA `ee123151941e3e65abb36fe76daf3aa5e172b535` proved owner-attested per-M03 lower cap and compact pre-Final DEEP20 (VPS **31/31 offline PASS**). PR#103 main SHA `bb399fa070e9a5dfd4f0f06742d0676dd85d3354` makes that **lowest cap durable in the same SQLite file**, including V2 processes with no M03 environment flag. Source GitHub Actions M01 **1106/1106 tests PASS** ([run 41653450601](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41653450601)), M07 targeted nonlive GREEN ([run 41653450590](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41653450590)). **Owner VPS pinned source recheck now PASS** on exact `bb399fa070e9a5dfd4f0f06742d0676dd85d3354`: **25/25 original V2 scoped OFFLINE tests** (1.885s, `M10_05_V2_GLOBAL_CAP_VPS_OFFLINE_PASS`), 4 Nov 2026. V2 latest-source VPS column ✅. This is *not* proof every V2 live process has been upgraded to that source.
2. **EGRESS IP-WIDE / ALL PEERS**: neither the V2 local SQLite nor owner `0600` JSON automatically counts V1/Hermes/NAT peers, different system users, other hosts, manually initiated REST, or independently identifies the outbound public IP. `all_shared_ip_consumers_accounted=true` is **only an operator assertion**, not independently obtained telemetry. Without infrastructure-owned egress evidence and full peer inventory, mark **UNVERIFIED**, keep M03 timer OFF, and do not tick full M10-05.

## Evidence required for IP-wide acceptance (outside repo work excluded)

- An independently established outbound public IP for the relevant VPS path and the list of every REST consumer sharing that IP, including other users/hosts/NAT if applicable; account for *unknown consumers* explicitly. **Do not access, change, stop or remove V1/Hermes projects/services from this work**.
- A verifiable gateway/firewall/egress meter, exchange-provided accounting if available, or infrastructure operator-approved packet/API counter that establishes **real rolling 60-second request demand** to Indodax for all external consumers. Identify the source, time range and completeness, not just average requests/minute, scanner estimates or total network bytes.
- Conservative peak *external* demand at the relevant 60-second horizon. Local V2 effective reservation capacity is at most `168 - external_peak_requests_rolling_60s` per rolling 60 seconds; **168** is the existing V2 conservative cap, not a verified IP-wide exchange quota. If peak unknown or capacity nonpositive, no scheduled scanner admission.
- Every cooperating real V2 public REST process needs proof of using the **same SQLite budget path** and source version enforcing the sticky cap. New consumers/paths can invalidate a prior inventory.
- The short-lived owner-only policy must be current (max 900 seconds), 0600, correctly scoped and separately approved; synthetic `8.8.8.8` from tests is NOT a real public IP. Revalidation is performed before each M03 GET. Expired/invalid/missing policy always denies original scheduled scanner.
- **Important operational behavior:** once the V2 shared SQLite cap decreases, code from PR#103 cannot automatically increase it, even when an external policy expires. It needs an explicitly reviewed, bounded maintenance/release procedure; do not delete/recreate/modify the quota SQLite by hand. A conservative stuck-low quota is preferable to unapproved request oversubscription.

## Original V2 local read-only diagnostic, NOT external peer proof

The original V2 diagnostic `indodax_ai_bot.m10_egress_readonly` opens only the default **V2 user** SQLite namespace under `~/.cache/indodax-ai-bot-v2/rest-rate.sqlite3` through an immutable read-only connection. It does not start systemd, create tables, reserve a GET, query an API, inspect other repos, read owner secret policy, or output raw DB paths/IP/token. Execute it from the **source already present in V2**:

```bash
(
  set -euo pipefail
  ROOT="$HOME/indodax-ai-bot-v2"
  cd "$ROOT"
  "$ROOT/.venv/bin/python" -B -m indodax_ai_bot.m10_egress_readonly
)
```

If the installed checkout does not yet contain this module, do **not** replace/restart the active runtime. Test with the pinned exact GitHub source from a temporary detached worktree and keep `HOME` unchanged for the read-only DB lookup. Never paste a credential or private policy into chat. A later guide will contain the exact merged SHA once tested.

**Safe output fields:** `database_present`, `rolling_60s_v2_reserved_requests`, `durable_v2_cap_requests_60s`, `caller_env_override_declared`, `snapshot_wal_possible`, a fixed `status` and four explicit proof booleans set to **false**. `caller_env_override_declared` reflects only the diagnostic shell environment, not a systemd process's environment. No active consumer is inventoried or proven to use the same DB.

**Limits:** `immutable=1` avoids WAL/SHM writes. If a WAL or journal sidecar exists, output is `V2_LOCAL_DB_ACTIVE_JOURNAL_SNAPSHOT_UNRELIABLE` and counts stay null. Even without sidecars, this is only a point-in-time **local reservation** count, not completed network requests nor upstream exchange IP accounting. A missing cap or missing DB never implies zero external demand. Regardless of local values, `m10_05_full_acceptance=false` and `m03_timer_go_authorized=false`. Until independent infrastructure-owned all-consumer egress counters are obtained, **full M10-05 remains ☐**.

## NEXT one gate

**Single remaining acceptance dependency for M10-05:** independently obtain infrastructure-level evidence identifying every consumer sharing the egress IP and their actual **rolling-60s REST peak** (not V2 SQLite-only reservations), plus observe that cooperating V2 runtime clients share the same quota file/cap. Treat unknown demand, unknown IP or unknown peer scope as **UNVERIFIED**. Do not inspect or mutate V1/Hermes/other repos, start scanner timer, alter quota DB, enable GO, private GET, paid AI or orders.
