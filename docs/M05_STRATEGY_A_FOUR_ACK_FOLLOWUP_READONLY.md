# M05 Strategy A: four-ACK 5m/15m price-only follow-up

**Owner scope, 12 November 2026:** original V2 M05 PID \`588864\`,
four natural priority ACKs at 11:01–11:08 WIB for
\`aaplx_idr\`, \`nbtnew_idr\`, \`neon_idr\`, \`anoa_idr\`. Original PR #287
already verified each ACK against as-of archived WS3 momentum rise ≥1.5%
**AND** rolling 24h turnover ≥Rp400m. This follow-up does not update any
strategy threshold or M03/M05 timer/service.

## Why recheck

The first original VPS audit found one evaluable five-minute price proxy
(NBTNEW +360.13bps), and the second found one evaluable fifteen-minute
proxy (AAPLX -740.76bps). All others were incomplete or immature when
audited. Price-only outcomes use the first retained public quote within
90 seconds **after** the Bot API ACK and within 90 seconds **after** the
five-/fifteen-minute horizon. Neither represents a maker fill, taker sale,
net ROI, actual position, real stop-loss or exchange order.

The existing \`inspect_postack_quality_readonly\` already enforces original
private source permissions, source-ACK matching, bounded time windows, no
lookahead, distinct missing-data statuses and optional 72h research archive.
The thin \`scripts.m05_strategy_a_four_ack_followup_readonly\` wrapper
checks the **same original M05 process PID 588864** (no restart allowed),
original M03 timer, actual M05 priority flag and NO/NO/DRY_RUN, and the
four exact ACK-backed pairs. It rejects incomplete, extra, duplicated,
non-ACK and unauthenticated-source rows. It summarizes measurable 5m/15m
price direction separately from **not mature**, **no fresh post-ACK quote**
and **no fresh horizon quote**. It never reports a trade win rate.

## Owner VPS run, no runtime modifications

First fast-forward a clean original V2 \`main\` to the exact GitHub commit
communicated after this PR is merged, with M05 and M03 still active. Then:

\`\`\`bash
cd "$HOME/indodax-ai-bot-v2"
export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN
.venv/bin/python -B -m unittest -q \
  tests.test_m05_strategy_a_four_ack_followup_readonly \
  tests.test_m05_signal_edge_audit_readonly
.venv/bin/python -B -m scripts.m05_strategy_a_four_ack_followup_readonly
\`\`\`

Successful result:
\`M05_STRATEGY_A_FOUR_ACK_PRICE_FOLLOWUP_READONLY\`, exactly four
ACK/source-verified rows, 5m and 15m per-pair proxy statuses, separate
observed/missing counts, and \`original_m05_pid=588864\`.
A blocked result is \`M05_STRATEGY_A_FOUR_ACK_FOLLOWUP_BLOCKED_READONLY\`.
**Never restart M05 to force the old PID**, and never send forced alerts.
A new process requires a separately pinned scope and delivery receipt audit.

No HTTP, Telegram send, AI, trade, systemd edit, database update or extra
socket occurs. The script does NOT access/expose Telegram tokens, owner
ID, Bot API message ID, raw process environment or private paths.

The existing WS3 72h research archive has partial coverage and bounded
retention. If a pair or horizon is still missing, it remains **UNKNOWN**,
not a flat price, loss, successful trade or failure of Strategy A.
Do not modify production thresholds based on four events. For the next
design iteration prioritize real trade-side BUY/SELL evidence, bid-ask
spread/depth, freshness, independently confirmed MARKET/RISK/STRATEGY,
and owner's order-specific manual approval while hardlock remains on.
