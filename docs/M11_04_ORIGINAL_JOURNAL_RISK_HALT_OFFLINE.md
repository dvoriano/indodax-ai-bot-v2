# M11-04 | Persistent OFFLINE risk-halt contract in original V2 journal

**Owner-independent source only, no deployed LIVE release.** The work follows
[M11-04 static gap map](M11_04_STATIC_RELEASE_SAFETY_AUDIT.md)
without adding another bot or a second SQLite database.

**Source:** [PR#131](https://github.com/dvoriano/indodax-ai-bot-v2/pull/131),
merged code SHA `927f14f1fb63b61edc69452fac000996e7b51bb7`.
**GitHub CI:** [M01 Actions 41765994133](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41765994133)
**1176/1176 PASS** including 19 new focused offline tests and safe CLI PASS.
[M07 original source targeted 41765994161](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41765994161)
GREEN. **Owner VPS original V2 detached source verification COMPLETE (4 Nov 2026): 19/19 PASS.**

## What is implemented, and what it does NOT mean

This source extends the existing original
`indodax_ai_bot.journal.DecisionJournal` SQLite schema, **not** a
separate ledger. All three new tables are in the same original journal:

- `m11_offline_risk_limits`: an immutable, single-owner **owner-entered
  but unverified** policy scenario covering max aggregate proposed
  capital, max open orders and max daily realized loss.
- `m11_offline_risk_halt`: singleton halt with state only `ENGAGED`;
  persisted on journal creation, **no clear/unhalt/release method**.
- `m11_offline_risk_halt_events`: uniquely keyed, replay-safe, monotonic
  emergency/recovery records. Repeated event ID with different contents
  is a conflict, not a new approval.

The original journal APIs
`record_m11_offline_risk_limits_once`,
`engage_m11_offline_emergency_halt_once` and
`review_m11_offline_risk_halt` are **isolated offline contract APIs**.
No normal original CLI, scanner, Telegram, owner Final, M07 adapter,
private exchange API, paid AI, or installed systemd unit invokes them
to permit LIVE orders. Tests show concurrent same-ID events/policies
produce one durable insert and retries never become second writes.

The review evaluates **unverified scenario inputs only**:
existing exposure + proposed BUY cost versus scenario cap, open order
count versus maximum, and scenario realized net loss versus an explicit
WIB-date threshold. If any snapshot is missing or malformed, it fails.
A scenario that passes arithmetic still produces
`REJECT_OFFLINE_RISK_HALT_AND_UNVERIFIED_SOURCES` with
`execution_allowed=false`,
`owner_policy_signed_verified=false`,
`scenario_is_real_exchange_evidence=false`,
`kill_switch_installed_runtime_verified=false`,
`m11_04_full_acceptance=false` and
`m11_live_release_authorized=false`.
The contract **cannot clear halt or issue BUY/SELL/CANCEL**.

**Not established:** authenticated owner policy, account fingerprint,
genuine exchange capital/exposure, filled trades/fees/settled IDR, net
realized PnL, installed LIVE kill switch, external G1 attestation,
original M05 order Final, atomic real order gate, runtime source pin,
least-privilege secrets, protective SELL, restart/recovery on real
exchange, or LIVE GO. This is a **pre-release architecture primitive**,
not full M11-04 acceptance.

## Owner VPS scoped offline verification (COMPLETED 4 November 2026)

**ACCEPTED OWNER VPS EVIDENCE, do not rerun:** owner verified exact original V2 detached SHA `927f14f1fb63b61edc69452fac000996e7b51bb7`; marker `M11_04_RISK_HALT_ORIGINAL_SOURCE_SHA=927f14f1fb63b61edc69452fac000996e7b51bb7`. Ran `tests.test_m11_04_original_durable_risk_halt` under `NO/NO/DRY_RUN`: **`Ran 19 tests in 1.534s`, `OK`**, marker `M11_04_OWNER_RISK_HALT_OFFLINE_PASS`. All 19 original journal tests passed using independent temporary SQLite fixtures, with no installed runtime database, M03/M05/BTC WS service, external helper, Telegram/network/paid AI or trade. This validates scoped offline functionality only; it does NOT authenticate owner policy, prove real loss caps/installed kill switch, or authorize LIVE.

The historical command below is retained to document the exact bounded proof. Do **not** repeat it.

The accepted owner command ran only the new **19-test** module from exact pinned source using a
temporary detached worktree. Do **not** `git pull` or reset the original
V2 `main` checkout. This script does not open any live runtime SQLite
file. Tests create their own isolated temporary journal under `/tmp`.

```bash
(
  set -euo pipefail
  umask 077

  ROOT="$HOME/indodax-ai-bot-v2"
  SHA="927f14f1fb63b61edc69452fac000996e7b51bb7"
  TMP="$(mktemp -d /tmp/v2-m11-risk-halt-XXXXXX)"
  WT="$TMP/code"

  cleanup() {
    git -C "$ROOT" worktree remove --force "$WT" \
      >/dev/null 2>&1 || true
    rm -rf "$TMP"
  }
  trap cleanup EXIT

  echo "===== VERIFY PINNED ORIGINAL V2 SOURCE ====="
  GIT_TERMINAL_PROMPT=0 timeout 30s \
    git -C "$ROOT" fetch --no-tags origin main
  git -C "$ROOT" merge-base --is-ancestor "$SHA" FETCH_HEAD
  git -C "$ROOT" worktree add --detach "$WT" "$SHA"
  test "$(git -C "$WT" rev-parse HEAD)" = "$SHA"

  echo "M11_04_RISK_HALT_ORIGINAL_SOURCE_SHA=$SHA"
  echo "===== ORIGINAL V2 DURABLE OFFLINE RISK HALT ====="

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
      tests.test_m11_04_original_durable_risk_halt
  )

  echo "M11_04_OWNER_RISK_HALT_OFFLINE_PASS"
)
```

A `Ran 19 tests` / `OK` plus matching SHA/marker proves only
the original owner VPS test fixture can reproduce the GitHub CI result.
It does not deploy any safety gate, amend M11-04 acceptance, release
LIVE, or prove real account risk/daily PnL. Do **not** rerun accepted
old 12/12 M11-04 tests, full 1176/1176 CI, M09, or M10 probes.

**Operational boundaries:** no BTC WS restart, guard tracing or
out-of-V2 script access; no M05 deployed path follow-up when installed
WorkingDirectory differs from original root, no service/Telegram
cursor/ledger changes. M03 timer OFF. LIVE, private GET, paid AI and
BUY/SELL/CANCEL OFF.

**Roadmap:** M11-04 complete `☐`, M11 **0/5** complete, M10 **4/7**
complete, M09 **0/4** complete. Source/CI checks are not full release
approval.
