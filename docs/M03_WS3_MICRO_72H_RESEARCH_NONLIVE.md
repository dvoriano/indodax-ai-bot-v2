# M03 microstructure prospective 72h research archive | owner Strategy A

**Scope:** original \`indodax-ai-bot-v2\`, nonexecuting source-evidence extension.
On 12 November 2026, four valid priority Telegram ACKs (AAPLX, NBTNEW,
NEON, ANOA) all lacked retained as-of M03 microstructure rows at the
retrospective audit. Their data may never have been sampled or may have
expired from the existing M03 micro table after ~2h. This cannot be
reconstructed retroactively.

The original M03 WS3 watcher already captures a **maximum of two source
micro pairs per cycle**, with partial trade frames and top-five book snapshot;
the existing optional WS3 72h price research DB separately archives public
24h summary snapshots. These are distinct source types.

## Added in this PR

- New \`indodax_ai_bot/m03_ws3_micro_research_archive_nonlive.py\`
  archives **only actual captured** original WS3 micro rows in the **same**
  already-used private \`~/.local/state/m03-ws3-evidence/observations.sqlite3\`,
  new table \`ws3_micro_research_evidence\`. No second bot or scanner DB.
- Research rows are immutable within a pair/minute, timestamped at original
  local capture, retained for **at most** 72h and 20,000 rows. The original
  2h operational micro table and 24h summary/alert behavior are unchanged.
  Disk capacity / high-water pruning may shorten availability.
- Preserves source-reported BUY/SELL counts/notionals, timestamp, gap/
  truncation flags, best bid/ask, spread, top-five depth and book offset.
  Missing book imbalance remains SQL NULL, not fabricated as zero.
- Source-scope checks enforce ORIGINAL observed WS3, ≤2 micro pairs,
  no future source timestamps, private 0700 directory and 0600 regular
  SQLite file, no symlinks, no future trade/book capture, no G1 or
  independent aggressor-side attestation. Incomplete source remains UNKNOWN.
- Uses **exact same already received** WS3 snapshot. Adds **zero**
  subscriptions, API requests, Telegram messages, order actions, AI calls
  or SELL/BUY permissions. Independent best-effort failures cannot turn
  a successful original M03 watch tick into an order or lost WATCH.
- Separate guard \`M03_WS3_MICRO_72H_ARCHIVE_GO=YES\` is required **in
  addition to** existing \`M03_WS3_EVIDENCE_ARCHIVE_GO=YES\` and the
  existing M03 \`micro_enabled=True\` source path. The new flag defaults
  OFF and no systemd file is modified by this PR.

**This branch does not enable the new flag on the original VPS.**
A clean pinned source deployment alone keeps the new micro research writer
disabled. Original M03 and M05 services keep their existing settings.
Do not enable any new flag before reviewing existing environment sources,
service state, journal source consistency and production-readonly gates.

## What it does NOT prove

Coverage of all pairs, continuous trade tape, true aggressor BUY dominance,
closed 1m candles, 5/15m future returns, executable bid depth at order time,
maker fills, three actual AI decisions, or strategy profitability. A
snapshot obtained after a Telegram ACK cannot prove what was known before
the ACK. Only micro records with \`recorded_local_epoch <= original claim
epoch\` can qualify for prospective as-of audit. Capturing two pairs per
cycle can still miss an alerted pair.

## Offline tests

\`\`\`bash
cd ~/indodax-ai-bot-v2
export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN

.venv/bin/python -B -m unittest -q \
  tests.test_m03_ws3_micro_research_archive_nonlive \
  tests.test_m03_ws3_evidence_archive_nonlive \
  tests.test_m03_ws_summary_live_watch
\`\`\`

Tests cover original 2-pair cap, strict privacy, future/forged observations,
72h/20k retention, empty/missing book and trade semantics, off-by-default,
both opt-ins, reusing original collector without extra requests, and
best-effort archive failure preserving original WATCH.

**Acceptance:** only GitHub CI and original VPS source tests before a
separate explicit owner opt-in. Next read-only follow-up is to measure
prospective captured-pair coverage against natural M05 ACKs using as-of
recorded micro rows; do NOT mark M03/M04/M07/M08 live acceptance complete
from source-only data.
