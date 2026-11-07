# M04: Original M03 Radar to MARKET/RISK/STRATEGY (read-only handoff)

## Status and owner controls

The original Indodax V2 M03 public scanner and original M05 Telegram radar remain continuously running under their standing public-only authorizations. The owner /ai screenshot on 7 Nov 2026 shows protected local Sumopod configuration `glm-5.3-flash` for MARKET, RISK and STRATEGY. This does **not** establish that the models exist online, that a response has been received, that G1 issuer is authenticated, or that permission to spend AI tokens is granted. No BUY/SELL/CANCEL is permitted.

This source-only bridge **does not instantiate SumopodChatAdapter or ThreeRoleShadow** and cannot send messages or orders. The existing `ThreeRoleShadow` separately requires an independently authenticated G1 receipt before *any* real role caller, so this public-only handoff MUST NOT be interpreted as bypassing it.

## Data flow

`indodax_ai_bot.m04_radar_three_role_handoff_readonly` calls the existing `inspect_original_m03_radar_readonly` ONCE, reading original private V2 SQLite minute-ticker samples through that inspector (`mode=ro; query_only=ON`). No extra market REST/WS request, new scanner, private account GET, owner SQLite write, Telegram action or network operation.

The bridge validates all FIVE possible current M03 Top5 watches first, then fills up to TWO role-input slots by the existing deterministic pre-AI priority (rising-price observational watches before falling-price rolling-delta-only watches); ties preserve original M03 radar rank. This is a quality-aware input order ONLY, never an alteration of M03/M05 live radar alerts, thresholds, G1 eligibility, paid AI dispatch or trading permissions. Selected packets retain original radar rank and observed pre-AI priority rank. Up to TWO selected watches are converted to independent canonical SHA-256 evidence packets containing only observed sample price, 2–5 minute observed price delta, rolling 24h turnover/delta, spread and sample-gap flags. Packets MUST be no older than 90 seconds, contain valid non-duplicate IDR pairs, match original pre-AI review fingerprint, and preserve the distinction between *rolling 24h volume deltas* and *actual one-minute executed volume*. The selected evidence digest is shared across exactly three uncalled role slots (`MARKET`, `RISK`, `STRATEGY`). The digest is **not** a G1 issuer attestation.

Each packet explicitly states unavailable closed 1m/5m/15m candles, genuine one-minute traded volume, full trade tape, verified depth, authenticated news/maintenance, account balance, owned lot/entry and all-in fee/slippage. Pre-AI screening can describe priority but **cannot invent AI verdicts**. Source-only RISK stays `NO_TRADE_SOURCE_AND_ACCOUNT_UNVERIFIED`, STRATEGY stays `WAIT_NO_AUTHENTICATED_ENTRY_AND_NET_COSTS`, and normal BUY requires **both Rp10.000 net AND 2% net** on the same authenticated position.

## Owner VPS acceptance command (no restart, no paid calls)

```bash
cd "$HOME/indodax-ai-bot-v2"
export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN

.venv/bin/python -B -m unittest -q \
  tests.test_m04_original_radar_three_role_handoff_readonly

.venv/bin/python -B -m \
  indodax_ai_bot.m04_radar_three_role_handoff_readonly
```

Outputs are observational, not genuine provider responses. `M04_RADAR_HANDOFF_NO_CURRENT_WATCH` is correct if no original current Top5 candidate exists.

## Next gates before any paid MARKET/RISK/STRATEGY calls

1. Independently verify the original exchange issuer G1 receipt over the exact same source, complete relevant public market evidence and downstream RISK/account/fee data. A public ticker price and rolling volume delta **alone** are insufficient.
2. Add explicit owner-specific paid AI provider GO with owner-selected model/pricing and valid durable usage guard. Do not automatically select budget or models.
3. Pass the independently signed immutable source and three-role evidence through existing `ThreeRoleShadow` verifier. RISK blocker cannot be overwritten by MARKET or STRATEGY.
4. Test correct owner-only advisory result and fresh-source TTL in the existing M05 receiver; no new bot/worker or order authorization.
5. Obtain separate manual order/live release only when owner chooses and account/fee/order safeguards pass.

This file documents a handoff milestone, **not** activation of the paid three-agent runtime.
