# M05 Strategy A Priority Alert: safe read-only preflight

Status: **PRE-CUTOVER CHECK ONLY**. The owner-approved proposal requires
**sample-to-sample rise >=1.5% AND rolling 24h turnover >=Rp400m**, while the
original M03 early 0.35% scout and 150m floor remain unchanged. Neither the
source threshold nor this preflight is an order/BUY recommendation.

Original V2 M05 already supports opt-in \`M05_RADAR_PRIORITY_V2_GO=YES\`
in its long-running service process. A source-only Git pull **does not**
change the environment inside a previously running PID. Do not assume the
filter is running because the source file contains its implementation.

## Preflight goal and safe usage

Only after the original owner VPS has a clean pinned main source (including
PR #283 and #284), execute:

\`\`\`bash
cd "$HOME/indodax-ai-bot-v2"
export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN
.venv/bin/python -B -m scripts.m05_strategy_a_priority_preflight_readonly
\`\`\`

A successful output contains:

- \`status=M05_PRIORITY_PREFLIGHT_READY_NO_CHANGE\`;
- original M05 active, stable PID, simple service, original workdir,
  clean main, the existing fast-longpoll drop-in and no daemon-reload needed;
- original M05 legacy timer disabled/inactive, and M03 mainpath timer active;
- active M05 process actually holds trading \`NO/NO/DRY_RUN\`,
  \`M05_TELEGRAM_FAST_LONGPOLL_GO=YES\`,
  \`M05_RADAR_MOMENTUM_ALERT_GO=YES\` and
  \`M05_WS3_RADAR_WATCH_GO=YES\`;
- priority opt-in is **NOT already enabled** in the running M05 process.

The script reads only fixed source/service metadata, the running M05 PID,
and allowlisted flags from its own \`/proc/PID/environ\`. It does not print,
log, retain or return any other environment variable, Telegram token,
private key, API credential, owner ID, or database content.

If any source flag, timer, permissions, or process check is incomplete, it
returns \`M05_PRIORITY_PREFLIGHT_BLOCKED_NO_CHANGE\` and exits nonzero.
A blocked preflight is **not** permission to blindly edit env files or restart
M05. Inspect the specific redacted reason and correct safely.

The script is **purely read-only**: no source or systemd change, PID
restarts, Telegram send, private HTTP, paid AI, BUY/SELL/CANCEL or trading.
A successful preflight is **NOT approval** to restart M05. Owner-specific
cutover to enable the flag must separately preserve the existing original
M05 receiver with a single controlled restart, a saved rollback path,
and verification of heartbeat, owner-private notification, unchanged M03
scanner, and all three original no-trade flags. Never restart a second
Telegram consumer or turn on live execution.

## Evidence and acceptance separation

- PR #283: code-level M05 priority filter, unit tests, original owner VPS
  targeted 66/66 proof, but original M05 process unchanged.
- PR #284: durable original DecisionJournal **offline STOP shadow**,
  original owner VPS targeted 77/77 PASS and same M05 PID, but no live SELL.
- This file and preflight script: **no deployment activation**. Must separately
  be source-tested on GitHub and owner VPS before an actual rollout.

Rolling 24h turnover is not 1-minute executed turnover or buy aggression,
and >1.5% sample rise is not necessarily an independently verified candle.
Guard all subsequent entry/exit decisions with independent data quality,
fees, order-book depth, actual bot-owned inventory, risk and owner Final
decision. Trading remains NO/NO/DRY_RUN.
