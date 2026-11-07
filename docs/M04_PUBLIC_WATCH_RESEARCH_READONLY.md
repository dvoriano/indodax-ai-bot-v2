# M04 PUBLIC WATCH Research Contract · Original V2, offline

## What was built

This module makes **deterministic source inspection** from the original M03 radar evidence and prepares three *non-model* research views. It does not call the GLM models, the Sumopod API or the `ThreeRoleShadow` class.

- Entry point: `indodax_ai_bot.m04_public_watch_research_readonly.inspect_original_public_watch_research_readonly`.
- Calls the existing original `m04_radar_three_role_handoff_readonly.inspect_original_radar_three_role_handoff_readonly` once. That calls original M03 radar SQLite inspector once, **read-only**, no new Indodax REST/WS collection and no new timer.
- Each of up to 2 candidate packets must be younger than **90 seconds**, match the canonical SHA-256 packet digest, carry all 11 known missing evidence categories, and bind all three as-yet-uncalled role slots to the *same* digest.
- A role research view always carries `kind=DETERMINISTIC_SOURCE_INSPECTION_NOT_AN_AI_RESPONSE` and `model_invoked=false`. Any injected model output, fabricated G1 status, stripped missing evidence, price/digest conflict, stale source or unauthorized execution flag is rejected.
- **MARKET research view**: observed direction of price between two timestamps, true observed spread and rolling 24h ticker delta only. Positive rolling 24h delta is **not** real 1m trade volume, a buy/sell imbalance or proof of continuation. Falling quote with growing rolling 24h turnover remains a caution.
- **RISK research view**: always `PUBLIC_RESEARCH_ONLY_NO_TRADE`; explicitly lists unavailable data, source gap, wide spread, and falling/flat price cautions. It is not a true AI risk decision.
- **STRATEGY research view**: always `WAIT_NO_VERIFIED_ENTRY_FEES_AND_CAPITAL`; estimated profit and ROI are `null`. Normal BUY requirements remain *both* Rp10,000 net and 2% net. No entry price, order size or targets are invented.

Neither the SHA digest nor role-view consistency authenticates the Indodax exchange issuer. The original `G1ReceiptVerifier` and `ThreeRoleShadow` are **not bypassed**, and no three genuine AI responses are claimed. The protected model IDs are owner-selected, not changed by this work.

## Owner VPS: one bounded read-only verification

```bash
(
 set -euo pipefail
 cd "$HOME/indodax-ai-bot-v2"
 export INDODAX_TRADING_ENABLED=NO
 export INDODAX_ALLOW_LIVE_EXECUTION=NO
 export INDODAX_EXECUTION_MODE=DRY_RUN
 .venv/bin/python -B -m unittest -q \
   tests.test_m04_public_watch_research_readonly \
   tests.test_m04_original_radar_three_role_handoff_readonly
 .venv/bin/python -B -m indodax_ai_bot.m04_public_watch_research_readonly
 systemctl --user show indodax-v2-m05-telegram.service \
   -p ActiveState -p MainPID -p NRestarts --no-pager
)
```

No restart. If source now has no Top5 candidate, `M04_PUBLIC_WATCH_RESEARCH_NO_CURRENT_CANDIDATE` and 0 role views is correct. To test a historically observed public SOL WATCH as of 7 Nov 2026 12:29:59 WIB, call `inspect_original_public_watch_research_readonly(now_epoch=<calculated WIB epoch>)` and inspect only safe role-view labels, not raw owner secrets or Telegram data.

## Gates not completed

1. Obtain independently sourced exchange/G1 coverage rather than using local hashes as issuer proof, and verify separate risk/account/fee prerequisites.
2. Obtain separate explicit owner authorization for **billable** Sumopod calls (model/pricing selected by owner), then add a limited, audited provider invocation path distinct from the qualified-trade path. There is **no default paid GO**.
3. Verify actual MARKET/RISK/STRATEGY model outputs with consistent digest and veto semantics. Zero calls here do not count as real AI.
4. Keep qualified trading decisions separate: owner-specific manual final click only in a later independently authorized phase; BUY/SELL/CANCEL currently OFF.

Master ledger status stays unchanged until acceptance is genuinely met. 
