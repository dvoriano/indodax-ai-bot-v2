# Owner Strategy A | Maker entry, net -5% protective exit, rebound scout

Status: **OFFLINE REVIEW CONTRACT ONLY** (12 November 2026). No production
Telegram threshold, M03 minute scanner, M05 sender, M07 transport or M08
position runtime was switched. This branch must not be deployed as a live
order workflow. API is Indodax Trade API V2, as documented at
https://github.com/btcid/indodax-official-api-docs/blob/master/INDODAX-TradeAPI-2.md.

## Decisions agreed with owner

| Stage | Rule | Order reference |
| --- | --- | --- |
| M03 early discovery | Keep current observational 0.35% acceleration / ≥Rp150m rolling 24h scouting | No order |
| M05 *priority* Telegram proposal | Sample-to-sample rise ≥1.5% **AND** rolling 24h ≥Rp400m | WATCH only |
| BUY | Rest at or below current best bid, not crossing ask; maker max spread 1.5% (research cap) | LIMIT **MOC** |
| T1 | Forecast net ROI ≥2% AND net IDR profit ≥10,000 for its own sale allocation | SELL LIMIT **MOC**, resting maker, not guaranteed fill |
| T2 | Forecast executable net ROI ≥8% AND net IDR profit ≥10,000 | SELL taker via price-bounded marketable LIMIT, after order reconciliation |
| T3 | Forecast executable net ROI ≥11% AND net IDR profit ≥10,000 | SELL taker via price-bounded marketable LIMIT, priority over T2 |
| STOP | Full remaining position net-exit estimate ≤-5% versus remaining buy cost including fees | protective taker; **does NOT require** profit floor |
| After STOP | Confirm all bot-owned SELL fills and returned spendable IDR | REBOUND_WATCH; no automatic BUY |

Net proceeds use **actual bid ladder**, deduct **SELL fee once** and
conservative **slippage once**, then compare to remaining allocated BUY cost
including BUY fee. The proposed T1 maker price uses best ask as **resting
quote reference only**, NOT realizable bid proceeds and NOT realized PnL.
Spread on profit taker proposals must be ≤0.5% in the initial test policy;
protective STOP takes precedence even in wide spread but still requires
bounded executable liquidity / limit-price and order safety.

## Protective state machine, mandatory for future implementation

1. Real bot-origin BUY fills establish ownership and cost; absent these
   records, all functions remain review-only. Protect confirmed partial BUY
   fills and cancel/reconcile unused BUY remainder on STOP.
2. On a fresh, fully depth-covered, *full remaining quantity* executable
   ROI ≤-5%, atomically persist a stop latch before any order operation.
   Once latched, a price rebound never resets the stop decision.
3. Inspect open BUY/SELL state and cancel/reconcile T1 or other resting
   orders, including partial fills. UNKNOWN / cancellation-pending blocks a
   conflicting SELL until proven resolved. No duplicate SELL/client IDs.
4. Submit protective price-bounded taker SELL **only after separate genuine
   authorization** and verified sellable bot-owned residual quantity.
   Reconcile partial fills, venue status, locked base, and wallet IDR.
5. Settle and record WIB net PnL from exchange-confirmed receipts. Only a
   verified CLOSED_SETTLED_IDR event permits handoff to M03 rebound radar.
6. Stop/source failure, stale book, insufficient depth, precision issues and
   exchange UNKNOWN demand a risk alert and no fabricated successful SELL.
   Stop triggering is **not** a promise of maximum 5% realized loss.

## Rebound WATCH only

Separate from held positions and never averaging down. After **verified**
settlement of the *prior* position and 15 minutes re-entry cooldown, require:

- At least three consecutive, closed 1m prices available *as of* evaluation
  (not sparse WS3 last-price interpolated into closed candles).
- Observed decline from original pre-drop as-of reference ≥7%; an eventual
  deeper decline than 15% is elevated risk, never a reason to add to a loss.
- Observed recovery from a **past** sampled low ≥1.5%, with the last two
  price steps rising.
- Rolling 24h turnover ≥Rp400m; maker spread ≤1.5%; fresh book and verified
  trade evidence. If any source missing, remain WATCH_UNCONFIRMED.
- New M04 MARKET/RISK/STRATEGY and owner-specific Final Decision, separate
  from the losing trade. No old approval reuse, no automatic rebuy.

These numeric filters are **prespecified research defaults** to validate
out-of-sample, not proven alpha. 24h rolling turnover is not actual executed
1-minute volume or verified aggressive BUY pressure.

## Implemented in this branch

- \`indodax_ai_bot/m07_order_adapter.py\` accepts explicit
  \`M07OrderDraft(time_in_force="MOC")\` for LIMIT and rejects MOC on MARKET.
  Existing callers stay default \`GTC\`. Adapter is STILL hardlocked.
- \`indodax_ai_bot/m08_strategy_a_nonlive.py\` is Decimal-only pure review:
  priority filter, maker BUY pricing, depth-based full STOP and tier exits,
  rebound as-of candidate, explicit UNKNOWN/reconcile and no execution.
- \`DailyScalpingOrchestrator.review_strategy_a_nonlive_once\` exposes the
  four pure checks through original V2 without writing orders or accounts.
- `indodax_ai_bot/m05_radar_momentum_nonlive.py` and
  `telegram_service_tick.py` now accept the **explicit** opt-in
  `M05_RADAR_PRIORITY_V2_GO=YES` to filter owner notices by both
  thresholds, leaving existing inspector/early scouting intact. Missing flag
  means **old owner alerts continue**; code is NOT enabled on original VPS.
- `tests/test_m05_ws3_radar_bridge_nonlive.py` covers the opt-in flow.
- `tests/test_m08_strategy_a_nonlive.py` covers thresholds, MOC, -4.99/-5
  /-7, stop latch, fee/slippage, open/UNKNOWN and stale book, full depth,
  per-tier Rp10k, T2/T3 priority and rebound missing-data protection.

## Explicitly NOT implemented or accepted

Production owner Telegram filter **not activated on original VPS**: existing M05 runtime
continues its older WS3 threshold. New opt-in requires a pinned safe source
cutover and process environment update with separate owner rollout verification.
No producer of independently proven 1m market tape/closed candles or
confirmed three real AI responses; no live trigger arbitration, durable
stop latch, real risk EXIT API, bot-owned fill settlement, order allocation,
fee tier verification, emergency automatic execution approval or VPS deploy.
Do not claim the tests confer these capabilities.

## Offline run / acceptance

\`\`\`bash
cd ~/indodax-ai-bot-v2
export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN
.venv/bin/python -B -m unittest -q tests.test_m08_strategy_a_nonlive
\`\`\`

Then run relevant existing M07/M08/WS3 compatibility tests and the normal
main-branch CI. Re-run on original VPS only after a clean, pinned source
update, preserving the original M03/M05 processes. A future release must
prespecify a separate real-data shadow canary and report missing outcomes,
maker nonfills, latency, fees, spread, stops, realized loss and rebound
false-positive rates before changing M05 alerts or enabling any order.
Checklist M08-01/04/05/06/07 acceptance stays OPEN.
