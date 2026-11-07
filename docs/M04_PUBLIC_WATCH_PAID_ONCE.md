# M04 owner-paid PUBLIC WATCH first live GLM test, original V2 only

## Actual owner authorization and outstanding cost input

On 7 Nov 2026, the owner explicitly authorized a **first billable PUBLIC WATCH test** for MARKET → RISK → STRATEGY, **NOT** qualified G1 trading, manual BUY/SELL/CANCEL, recurring paid AI, background jobs, model changes, or automatic cost limits. Owner had **not** yet supplied the monetary ceiling or the per-request worst-case reservation as of this source change. No permission is implied to invent either number, assume GLM availability, or assume provider pricing. **Do not execute paid CLI until owner supplies both cost limits and acknowledges personally verifying current provider prices.**

The original VPS most recently verified PR#224, 56/56 targeted tests PASS, prior historical SOL 12:29 WIB source has 3 test role slots, no actual AI responses, M05 PID 2990714 with zero restarts. An old historical SOL is **ineligible** for live paid analysis because PUBLIC WATCH evidence is fresh for at most 90 seconds.

## Code introduced

- `indodax_ai_bot.m04_public_watch_paid_once.run_public_watch_paid_once`: default OFF, never called by existing original M05, M03 or G1/ThreeRoleShadow. Requires exact owner confirmation token, explicit operator pricing acknowledgment, three **owner-selected** model identifiers, protected Sumopod API key, and `PersistentAiBudget` with positive operator-chosen `daily_limit_micro_usd` and `per_request_reservation_micro_usd`.
- `scripts.m04_public_watch_paid_owner_once`: explicit **operator-run CLI only**. Default invocation checks nothing private, spends nothing. Paid invocation requires ALL flags, reads original protected `~/.config/indodax-ai-bot-v2/m04/provider.json` and 0600 key using existing `owner_provider_source` and 0700 directory checks; no key on CLI or in output. Uses fixed private `m04/public-watch-paid-budget.sqlite3` with restrictive umask and 0600 file controls.
- Performs exactly **one existing original M03 read-only radar inspection**, validates entire fresh eligible cohort, then handles only **one** highest-ranked current candidate. Up to three sequential provider calls, strict MARKET WATCH/AVOID, RISK NO_TRADE, STRATEGY WAIT, same immutable public source digest, prior authenticated role responses carried to the next role only after validation. Reuses existing HTTPS-only Sumopod `_post`; no Telegram sending, private Indodax access, trading, restarts or new timers.
- Before **each** possibly billable HTTP request, atomically reserves the operator-specified per-request worst-case cost through `PersistentAiBudget` (uniqueness role+pair+digest, WIB day and daily ceiling). A failed/timeout request may still be charged, so reservations are NEVER refunded and a role/source is NEVER retried. On first malformed provider envelope, model mismatch, missing token usage, stale market source or illegal BUY/ALLOW/price-target response, the next role is NOT called. Operator's worst-case reservation **must be set from independently reviewed vendor model rates and input/output token assumptions**; micro-USD reservations are *ceilings for local gating*, not a guarantee of actual invoice/FX charges.
- Every role response requires a single completed JSON content, provider-returned model name matching selected model, strict role/pair/digest/missing-evidence schema, and explicit false G1/execution. Output may include safe role stance/reason and vendor-reported tokens. A real HTTPS transport is needed to establish provider-origin evidence; injected fake transports in tests always return `TEST_TRANSPORT` status and cannot claim genuine model results.
- Public-watch AI result is **informational only**. RISK remains NO_TRADE, STRATEGY WAIT; no `qualified_trade_proposal`, owner Final, Telegram order button or BUY/SELL authorization. It must **not** be connected to legacy `ThreeRoleShadow` qualified trading until genuine G1/full trade sources and separate release.

## Owner-run commands

**Safe offline preflight** after CI and source sync:

```bash
.venv/bin/python -B -m scripts.m04_public_watch_paid_owner_once
```

Result `M04_PAID_PUBLIC_WATCH_DEFAULT_OFF` with zero HTTP and no secret reads.

**Actual billable first test, ONLY AFTER owner supplies cost and personally reviews GLM model pricing** (illustrative placeholders are not amounts to use):

```bash
.venv/bin/python -B -m scripts.m04_public_watch_paid_owner_once \
  --execute-paid-once \
  --owner-confirm OWNER_PUBLIC_WATCH_PAID_TEST_ONCE \
  --owner-pricing-reviewed \
  --daily-limit-micro-usd OWNER_CHOSEN_DAILY_LIMIT \
  --per-request-reservation-micro-usd OWNER_CHOSEN_WORST_CASE_PER_CALL
```

The actual command MUST contain owner-supplied **integer** micro-USD values; never paste the placeholders as numeric values, and never share the API key. Equivalent dollars: **1 USD = 1,000,000 micro-USD**. The runner will run only for a **currently fresh** original M03 public WATCH. If none, it returns `M04_NO_FRESH_PUBLIC_WATCH_FOR_PAID_TEST` without billable requests; historical replay is never allowed for this paid CLI. To prepare for future natural candidates, do not create a second scanner/timer/Telegram poller. Simply retry manually later if owner explicitly chooses; do not install auto-cadence without a separate GO.

## Acceptance conditions for the paid milestone

1. Confirm current owner-selected model identifiers, current independently reviewed Sumopod prices, explicit per-request and daily operator cost ceilings, protected config file and dedup ledger writable under original owner.
2. Original V2 main source and new targeted tests PASS. Observe one currently fresh candidate, with no extra market fetches.
3. Confirm real provider HTTP response envelopes for all 3 models, exact validated role sequence/digest and provider-reported usage, durable budget reservation records, no unintended retries or restarts.
4. Public-only labels cannot become qualified trading/Final/BUY. All orders remain OFF. Owner can later decide on separate owner-private Telegram public WATCH AI report publication with dedup/TTL, but not in this first paid milestone.
5. Do not mark 3-AI production or any G1 execution checklist item DONE merely because one one-shot PUBLIC WATCH AI test succeeded.

Master 52/98 DONE until true checklist acceptance.
