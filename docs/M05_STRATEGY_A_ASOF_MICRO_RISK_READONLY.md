# Strategy A: original M03 as-of microstructure risk review, read-only

## Why

On 12 Nov 2026 the original owner VPS confirmed **4 natural M05 Bot API
ACKs**, and original M03 WS3 source archive reconstructed **rise >=1.5%
AND 24h IDR turnover >=Rp400m** for all four. After the ACK:

- AAPLX public LAST price fell **7.4076% at 15m**;
- NBTNEW rose **3.6013% at 5m**, then measured **-2.3548% at 15m**;
- ANOA fell **1.5716% at 15m**;
- NEON had **no fresh post-ACK starting price** for either horizon.

This does **not** prove 3/4 trades lost: no exchange BUY fill, SELL
liquidity, maker/taker fee, stop-loss execution, spread, slippage or AI
decision was observed. A 1.5% + Rp400m momentum **WATCH** should never
automatically become BUY.

## What is implemented

\`scripts.m05_strategy_a_asof_micro_risk_readonly\` reads the exact original
V2 private M05 ACK ledger for the fixed four pairs/claim window
11:00–11:09 WIB and the existing **original M03 private SQLite**
\`m03_ws_microstructure_watch_minute\` table in SQLite \`mode=ro\`.

It requires the same original M05 PID **588864**, running process priority
flag and **NO/NO/DRY_RUN**, original M03 timer active, protected original
owner/source DBs, four confirmed WS3 ACKs and no unexpected extra pair.
It selects micro capture **recorded locally no later than the original
alert CLAIM** for the same pair (recent original M03 minute archive).
Trade and book timestamps must not be in the future relative to claim.
Missing, stale, gap/truncated or malformed data is explicitly shown
rather than invented, and book midspread must agree with best bid/ask.

An **observational, uncalibrated research** risk review identifies:
1. Missing or stale as-of M03 capture;
2. Missing/gapped/truncated trade-side sample, not complete execution tape;
3. Missing/stale/inconsistent book;
4. Source-reported SELL amount >1.2x BUY amount (not aggressor verified);
5. Observed book spread >50 basis points or top-five bid depth below
   Rp1m, chosen only for *study*, not live entry rules.

Even with both samples and no listed concern, status is
\`ASOF_MICRO_PARTIAL_OBSERVED_NOT_BUY_PROOF\` with **zero BUY permission**
and **zero independent G1 attestation**. These are *not* actual
MARKET/RISK/STRATEGY model decisions or pre-trade risk approvals. No
favorable future price after the ACK is read or used for decision.

Original M03 micro capture selects **at most two pairs per cycle**, and
the original micro archive removes rows more than **~2h old**. If no
micro source remains for any of these historical four pairs, the correct
result is \`ASOF_MICRO_NOT_RETAINED_OR_NOT_CAPTURED\`: this is **not**
evidence they had no trades, no book, or failed a future entry test.

## Safe owner original VPS check

Once original V2 main is pinned to the reviewed commit with original M05
and M03 still active:

\`\`\`bash
cd "$HOME/indodax-ai-bot-v2"
.venv/bin/python -B -m unittest -q \
  tests.test_m05_strategy_a_asof_micro_risk_readonly
.venv/bin/python -B -m scripts.m05_strategy_a_asof_micro_risk_readonly
\`\`\`

The audit returns \`M05_STRATEGY_A_FOUR_ACK_ASOF_MICRO_RISK_READONLY\` on
complete process/claim verification, or
\`M05_STRATEGY_A_ASOF_MICRO_RISK_AUDIT_BLOCKED_READONLY\` when original
source, PID, ownership, service, or event scope is invalid. It does **not**
send Telegram, read exchange orders/balances, call AI, restart systemd,
write to SQLite, change M03 capture frequency, or enable BUY/SELL/CANCEL.

**Next iteration:** prospective source capture/replay with bounded 2-pair
coverage and source completeness to measure quality before momentum
alerts, then M04 genuine MARKET/RISK/STRATEGY handoff. Keep early scout
0.35% and M05 owner WATCH 1.5%+Rp400m unchanged until out-of-sample
evidence supports a change. Track real 24h running M05 and source alerts
separately from profit outcomes.
