# M04 PUBLIC WATCH: provider request preview and offline response contract

**Status:** source/CI-only integration. REAL Sumopod/GLM calls have not been made, and no billing or trading authorization exists.

## Why this is separate from qualified three-role trading

The existing `ThreeRoleShadow` correctly requires authenticated G1 and complete source/microstructure evidence before a **qualified trading** model decision. Original PUBLIC WATCH is an earlier informational signal from a public ticker and cannot satisfy that qualification. This module deliberately does NOT weaken G1 or use `ThreeRoleShadow` for public WATCH.

`indodax_ai_bot.m04_public_watch_provider_preview_nonlive` builds a **request preview** for an eventual informational PUBLIC WATCH provider call. It consumes the original M03→M04 handoff (which already validates original ticker evidence, max two candidates, source SHA, TTL, priority), and revalidates it through `prepare_public_watch_research_readonly`. For each candidate it constructs three non-sendable Sumopod Chat-Completions-compatible request bodies, with a common SHA256 source digest, specific role system instructions, the same source packet, all eleven missing fact classes, and explicit dependencies:

- **MARKET:** bounded `WATCH` or `AVOID`; no invented BUY flow or prices.
- **RISK:** only `NO_TRADE` while source/account/fee constraints remain unresolved.
- **STRATEGY:** only `WAIT`; no fabricated entry, target, profit, ROI or execution permission.

Each preview contains `send_allowed=false`, `owner_paid_ai_go_authorized=false`, `real_provider_dispatch_allowed=false`; `actual_prior_role_responses=null` so a real eventual caller MUST sequence MARKET→RISK→STRATEGY instead of inventing earlier outputs. No bearer key, private account, owner ID, or Telegram payload is included.

No model is selected automatically. If the caller does not provide three owner-selected model identifiers, `model=null`; if identifiers are explicitly supplied, they are **metadata for offline preview only**, not confirmation of vendor availability or permission to spend. The `SumopodChatAdapter` existing qualified trading path remains unchanged and unused.

`validate_public_watch_provider_response_shape_offline` can check text JSON with strict unique keys, exact pair/role/source digest, fixed eleven missing evidence acknowledgements, explicit G1 and execution false, reason length bounded, and role-restricted stance. This is **shape-only**; even if tests inject valid responses, no claim of actual model generation, provider receipt or cost is made. Model replies with `BUY`, `ALLOW`, targets, prices, ROI or extra fields fail closed.

## Original VPS verification

After this PR is merged, sync the original clean `main`; run:

```bash
(
  set -euo pipefail
  cd "$HOME/indodax-ai-bot-v2"
  export INDODAX_TRADING_ENABLED=NO
  export INDODAX_ALLOW_LIVE_EXECUTION=NO
  export INDODAX_EXECUTION_MODE=DRY_RUN
  .venv/bin/python -B -m unittest -q \
    tests.test_m04_public_watch_provider_preview_nonlive \
    tests.test_m04_public_watch_research_readonly \
    tests.test_m04_original_radar_three_role_handoff_readonly
  .venv/bin/python -B -m \
    indodax_ai_bot.m04_public_watch_provider_preview_nonlive
  systemctl --user show indodax-v2-m05-telegram.service \
    -p MainPID -p ActiveState -p NRestarts --no-pager
)
```

The CLI intentionally displays **counts and roles only**, not entire model prompts, secrets, user IDs or simulated inference results.

## Next gate for actual paid PUBLIC WATCH AI

The owner must separately authorize **billable** Sumopod requests after personally confirming model ID, pricing, per-request/daily controls and source restrictions. A future narrowly scoped provider runner then needs durable reservation, unique source deduplication, strict sequential binding, persistent sanitized audit and explicit distinction from G1-qualified trade decisions. This milestone does not enable such a runner. No original M03 scanner/M05 poller changes, no service restart, and no trade order.
