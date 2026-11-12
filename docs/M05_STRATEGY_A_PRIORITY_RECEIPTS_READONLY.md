# Owner M05 Strategy A: natural Bot API ACK and as-of WS3 evidence

Status: **read-only evidence audit, not a send test**. PR #286 activated
\`M05_RADAR_PRIORITY_V2_GO=YES\` in the original owner process with trading
hardlocked NO/NO/DRY_RUN. Original owner's subsequent VPS inspection verified
PID 588864, M03 active, flag active, and callback heartbeat ready. No
qualifying Telegram owner ACK or actual owner device receipt was established
by that verification alone.

## What the audit actually checks

The bounded command \`scripts.m05_strategy_a_priority_receipts_readonly\`:

1. Reads the active M05 service's PID and only the allowlisted running-process
   flags, plus the location of its existing original private owner ledger.
   Does not print env, token, owner ID, chat ID, or message ID.
2. Derives the running process start time from Linux /proc with no assumption
   about the earlier PID; inspects at most 100 existing delivery events from
   the **same original owner's** SQLite, in the current process window (max
   24h). An event before process start is not credited to the new opt-in.
3. Counts **ACKED** distinctly from **CLAIMED** and **UNCERTAIN**. Only the
   original Bot API ACK proves a Telegram API send was accepted; it does not
   establish that the owner viewed the message on a device.
4. Optionally reads the original M03 private 72h research archive
   \`~/.local/state/m03-ws3-evidence/observations.sqlite3\` in SQLite
   \`mode=ro\`, checking 0600 regular-file ownership and 0700 parent.
   Reconstructs only a partial as-of WS3 condition: exact source pair+
   source epoch, reference source minute 1–5m earlier, both actually recorded
   no later than the original M05 claim; then tests price rise ≥1.5% **AND**
   rolling24h turnover ≥Rp400m. No future row is permitted. It is **NOT**
   a recreation of M05's complete priority selection, source ranking, private
   sender input, trade-side flow, fee-aware ROI, or independent G1 attestation.
5. Reports REST ACKs as \`REST_SOURCE_NOT_RECONSTRUCTED\` and expired,
   mismatched or incomplete archive as **unknown/review**. Missing history
   never becomes invented PASS, economic loss or proof of a false Telegram
   alert. The research archive may contain fewer pairs than the original
   source due partial coverage and retention.

Run on the original VPS ONLY after an exact pinned main source update and
offline tests, keeping the M05 process intact:

\`\`\`bash
cd "$HOME/indodax-ai-bot-v2"
.venv/bin/python -B -m unittest -q \
  tests.test_m05_strategy_a_priority_receipts_readonly
.venv/bin/python -B -m scripts.m05_strategy_a_priority_receipts_readonly
\`\`\`

Return statuses:

- \`M05_PRIORITY_NO_NEW_ACK_YET_READONLY\`: the process is verified but
  no new confirmed ACK; this is neutral, not a failed service.
- \`M05_PRIORITY_ACK_EVENTS_OBSERVED_READONLY\`: original Bot API ACK(s) in
  new-process window; inspect as-of source verdicts separately. An ACK
  without reconstructible source remains unverified.
- \`M05_PRIORITY_ACK_AUDIT_BLOCKED_READONLY\`: original process/private source
  cannot be inspected safely. No service changes are permitted to bypass it.

There are **zero service restarts, API requests, Telegram sends, paid AI
requests or exchange orders** from this audit. It does not modify the
original M03/M05 DB, source files, systemd drop-ins or any owner settings.
The main priority alert may naturally send a Telegram notice in the meantime
independently of this read-only audit, but this command never triggers it.

Future proof to accept: at least one organically sent owner-private M05
Bot API ACK *after original priority cutover*, with source evidence meeting
the two threshold conditions as-of claim, and separate owner display
verification. Keep negative and missing cases rather than cherry-picking.
No M07/M08 actual stop/SELL/settlement checkbox is closed by this result.
