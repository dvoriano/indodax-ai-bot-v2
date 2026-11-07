# M04 PUBLIC WATCH · sequential MARKET → RISK → STRATEGY offline rehearsal

## Scope

Source-only bridge for the original V2 `indodax-ai-bot-v2`. User's original VPS showed **43/43 PASS** on M03 public radar→M04 provider preview, historical original SOL/IDR at **2026-11-07 12:29:59 WIB** has 1 public watch and three **unsubmitted** request previews. Original M05 continues active PID `2990714`, `NRestarts=0` per owner's last observation.

`indodax_ai_bot.m04_public_watch_role_sequence_nonlive.simulate_public_watch_role_sequence_offline` runs a **REHEARSAL ONLY** using the existing M03/M04 handoff plus explicitly supplied **prewritten TEST fixture JSON**. It does not create an API client or accept an arbitrary callback or network transport. Calling this module never contacts Sumopod or Indodax, changes the owner key/model/cost budget, sends Telegram, starts any new scanner, persists results, or creates an order.

This module is intentionally separate from qualified trading `ThreeRoleShadow` / `G1ReceiptVerifier` and does not weaken their source issuer or full trade tape requirements.

### What sequence is rehearsed

1. Valid original public M03 snapshot, original handoff max 2 coins, public-only evidence digest and 90-second sample TTL.
2. Original validated offline provider preview defines three exact role/pair/digest request contexts.
3. A caller-supplied **test fixture map** must match every selected coin and exactly MARKET/RISK/STRATEGY.
4. Process MARKET fixture, validate strict JSON schema; prepare an isolated *next test request context* with MARKET response already bound; process RISK fixture (only NO_TRADE), then STRATEGY fixture (only WAIT). No fixture is represented as a real GLM/Sumopod response.
5. Store only safe SHA-256 fingerprints of constructed TEST contexts and fixture responses, source pair/digest and role stances, never claim a model receipt, model pricing or an execution suggestion.
6. A stale/tampered source, missing or cross-pair response, G1/paid authority claim, RISK ALLOW, STRATEGY BUY, or invalid role aborts the entire rehearsed cohort. There is **no partial successful trading chain**.
7. No fixture supplied is normal production behavior: `M04_PUBLIC_WATCH_SEQUENCE_TEST_RESPONSES_NOT_PROVIDED` (or `NO_CURRENT_CANDIDATE`) with 0 real model calls.

### Owner VPS acceptance, read-only

```bash
(
  set -euo pipefail
  cd "$HOME/indodax-ai-bot-v2"
  export INDODAX_TRADING_ENABLED=NO
  export INDODAX_ALLOW_LIVE_EXECUTION=NO
  export INDODAX_EXECUTION_MODE=DRY_RUN
  .venv/bin/python -B -m unittest -q \
    tests.test_m04_public_watch_role_sequence_nonlive \
    tests.test_m04_public_watch_provider_preview_nonlive \
    tests.test_m04_public_watch_research_readonly \
    tests.test_m04_original_radar_three_role_handoff_readonly
  .venv/bin/python -B -m \
    indodax_ai_bot.m04_public_watch_role_sequence_nonlive
  systemctl --user show indodax-v2-m05-telegram.service \
    -p ActiveState -p MainPID -p NRestarts --no-pager
)
```

Expected 56 scoped tests (13 new + 13 provider preview + 17 research + 13 handoff). Live CLI intentionally has no fixtures, so it **will not generate three fake model results**. The real owner VPS research and source handoff have already been separately proven by previous checks.

## Separate gate to actual paid AI

Owner must explicitly approve billable **PUBLIC WATCH** inference, after independently checking the owner-selected models, current provider pricing, maximum per-run and total cost, no-repeat durable source/cost reservations, and the scope of the original owner-only Telegram publication. A production provider adapter needs separate explicit authorization, upstream response metadata/provenance validation, and safe handling of partial three-role failure. Any such model findings would still be observational WATCH only, **not** G1-qualified or order-executable. No GO is implied by running tests or opening a GitHub PR.

Master checklist remains unchanged until its original acceptance criteria actually pass.
