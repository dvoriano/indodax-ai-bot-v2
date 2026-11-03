# M10-05 | Verifikasi shared egress-IP tanpa menyentuh V1/Hermes

**Status 3 Nov 2026:** M10-05 **PARTIAL**. Original V2 scanner M03 remains staged/disabled, orders OFF, paid AI OFF. Do not turn on the timer or make private API/trading calls using this page.

## Dua bukti berbeda, jangan dicampur

1. **V2 LOCAL SHARED QUOTA**: original `IndodaxPublicMarket._get` always calls `SharedPublicRestBudget.reserve` before REAL public REST network. PR#100 main SHA `ee123151941e3e65abb36fe76daf3aa5e172b535` proved owner-attested per-M03 lower cap and compact pre-Final DEEP20 (VPS **31/31 offline PASS**). PR#103 main SHA `bb399fa070e9a5dfd4f0f06742d0676dd85d3354` makes that **lowest cap durable in the same SQLite file**, including V2 processes with no M03 environment flag. Source GitHub Actions M01 **1106/1106 tests PASS** ([run 41653450601](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41653450601)), M07 targeted nonlive GREEN ([run 41653450590](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41653450590)). Source after PR#103 needs its **own pinned scoped VPS recheck** before the checkout cell for the current source may be accepted. This is *not* proof every V2 live process has been upgraded to that source.
2. **EGRESS IP-WIDE / ALL PEERS**: neither the V2 local SQLite nor owner `0600` JSON automatically counts V1/Hermes/NAT peers, different system users, other hosts, manually initiated REST, or independently identifies the outbound public IP. `all_shared_ip_consumers_accounted=true` is **only an operator assertion**, not independently obtained telemetry. Without infrastructure-owned egress evidence and full peer inventory, mark **UNVERIFIED**, keep M03 timer OFF, and do not tick full M10-05.

## Evidence required for IP-wide acceptance (outside repo work excluded)

- An independently established outbound public IP for the relevant VPS path and the list of every REST consumer sharing that IP, including other users/hosts/NAT if applicable; account for *unknown consumers* explicitly. **Do not access, change, stop or remove V1/Hermes projects/services from this work**.
- A verifiable gateway/firewall/egress meter, exchange-provided accounting if available, or infrastructure operator-approved packet/API counter that establishes **real rolling 60-second request demand** to Indodax for all external consumers. Identify the source, time range and completeness, not just average requests/minute, scanner estimates or total network bytes.
- Conservative peak *external* demand at the relevant 60-second horizon. Local V2 effective reservation capacity is at most `168 - external_peak_requests_rolling_60s` per rolling 60 seconds; **168** is the existing V2 conservative cap, not a verified IP-wide exchange quota. If peak unknown or capacity nonpositive, no scheduled scanner admission.
- Every cooperating real V2 public REST process needs proof of using the **same SQLite budget path** and source version enforcing the sticky cap. New consumers/paths can invalidate a prior inventory.
- The short-lived owner-only policy must be current (max 900 seconds), 0600, correctly scoped and separately approved; synthetic `8.8.8.8` from tests is NOT a real public IP. Revalidation is performed before each M03 GET. Expired/invalid/missing policy always denies original scheduled scanner.
- **Important operational behavior:** once the V2 shared SQLite cap decreases, code from PR#103 cannot automatically increase it, even when an external policy expires. It needs an explicitly reviewed, bounded maintenance/release procedure; do not delete/recreate/modify the quota SQLite by hand. A conservative stuck-low quota is preferable to unapproved request oversubscription.

## Read-only local V2 diagnostic, NOT an external peer audit

This block queries the *default V2-owned rate database only*. It does **not** create/modify any database, call HTTP, read V1/Hermes, invoke systemd, or authorize M03 startup. If a non-default V2 quota path was configured, this result is **not** evidence of that path. It deliberately excludes IP addresses, tokens and external peer identities.

```bash
(
  set -euo pipefail
  ROOT="$HOME/indodax-ai-bot-v2"
  "$ROOT/.venv/bin/python" -B - <<'PY'
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

path = Path.home() / ".cache" / "indodax-ai-bot-v2" / "rest-rate.sqlite3"
result = {
    "scope": "V2_DEFAULT_LOCAL_SQLITE_ONLY",
    "external_peers_independently_verified": False,
    "shared_egress_ip_accounting_verified": False,
    "m03_timer_go_authorized": False,
    "quota_file_present": False,
    "rolling_60s_local_reservations": None,
    "sticky_cap_per_60s": None,
}
try:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_size > 33554432):
        result["status"] = "V2_DB_UNTRUSTED"
    else:
        result["quota_file_present"] = True
        # Opening RO does not authorize live requests or modify quota rows.
        with sqlite3.connect(path.absolute().as_uri() + "?mode=ro&immutable=1",
                             uri=True, timeout=1.0) as con:
            con.execute("PRAGMA query_only=ON")
            present = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "rest_get_reservations" in present:
                row = con.execute(
                    "SELECT COUNT(*) FROM rest_get_reservations "
                    "WHERE requested_at > ?", (time.time() - 60.0,),
                ).fetchone()
                result["rolling_60s_local_reservations"] = row[0]
            if "rest_shared_egress_cap" in present:
                row = con.execute(
                    "SELECT max_local_requests_60s FROM rest_shared_egress_cap "
                    "WHERE id=1"
                ).fetchone()
                result["sticky_cap_per_60s"] = row[0] if row else None
            result["status"] = "V2_LOCAL_ONLY_EXTERNAL_UNKNOWN"
except FileNotFoundError:
    result["status"] = "V2_DB_ABSENT_UNVERIFIED"
except (OSError, sqlite3.Error, ValueError):
    result["status"] = "V2_DB_READONLY_UNAVAILABLE"
print(json.dumps(result, sort_keys=True))
PY
)
```

**How to read:** A populated `rolling_60s_local_reservations` only shows requests **reserved in that V2 SQLite**, not upstream receipt or full egress/IP-wide usage. Another V2 process using a different DB is not counted. `sticky_cap_per_60s` being unset does not mean external demand is zero. If any evidence is unknown, M10-05 full acceptance remains `[ ]`.

## NEXT one gate

Owner VPS **pinned offline** recheck for PR#103 merged source `bb399fa070e9a5dfd4f0f06742d0676dd85d3354`, only changed quota/egress tests; then separately obtain independent infrastructure-level inventory/peak counters without touching external projects. No service starts, GO, private GET, paid AI or orders.
