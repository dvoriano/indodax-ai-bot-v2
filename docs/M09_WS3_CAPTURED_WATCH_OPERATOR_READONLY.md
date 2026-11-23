# M09 WS3 actual WATCH capture → 5/15/30/60m research (READ ONLY)

**Scope:** one-shot original M03 WS3 → original M04 *uncalled* input packet → existing M09 local gross-price outcome evaluator. This is a source-diagnostic research tool, not a new scanner, not an online worker, not an AI decision, and not trading PAPER fills.

## Why the capture must happen live

The minute summary archive can be updated after the original minute. Reconstructing a WATCH using an arbitrary historical `now_epoch` would risk selecting candidates with hindsight. A valid research baseline requires capturing an actual M04 handoff **while current**, preserving its per-pair source digest and three uncalled role input slots. The capture file is locally editable and does **not** prove exchange authenticity, independent G1, complete tape, or immutable third-party evidence.

## Original VPS, controlled operator-only use

Run only on the existing clean original V2 checkout after the target commit and focused tests have passed. No new long-running service, cron/timer, REST/WS connection or provider. All source reads use existing private original SQLite data. These commands create **only the operator's explicit 0600 JSON capture file**, never a PAPER position or an exchange order.

```bash
cd "$HOME/indodax-ai-bot-v2"
export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN
export PYTHONPATH="$PWD/tests${PYTHONPATH:+:$PYTHONPATH}"

.venv/bin/python -B -m unittest -q \
  tests.test_m09_ws3_watch_price_outcomes_readonly \
  tests.test_m09_04_original_virtual_paper \
  tests.test_m04_ws3_indexed_observation_nonlive

umask 077
CAPTURE="$HOME/indodax-ai-bot-v2/runtime/m09_ws3_watch_capture.json"
.venv/bin/python -B -m scripts.m09_ws3_watch_price_history_readonly \
  --capture-current > "$CAPTURE"
test -s "$CAPTURE"
test "$(stat -c '%a' "$CAPTURE")" = "600"
```

If no eligible WATCH is present, capture exits nonzero with `NO_ELIGIBLE_UNCALLED_WATCH`; do not treat that file as valid evidence. The existing scanner continues on its own. The command must be invoked again when a genuinely new WATCH occurs, with a new separate filename when preserving multiple independent observations. Do not repeatedly query old snapshots claiming they were captured earlier.

After the desired horizon, evaluate **the same** private snapshot, from the **same source DB path**:

```bash
.venv/bin/python -B -m scripts.m09_ws3_watch_price_history_readonly \
  --evaluate-capture "$CAPTURE"
```

For a different existing source path, pass identical `--source-db` in both calls. Evaluation searches the existing 5-second index up to 30 seconds after each of the four horizons, and only includes rows received by evaluation time. `NOT_YET_MATURED` and `UNKNOWN_NO_LOCAL_PRICE_NEAR_HORIZON` remain distinct. Observation is **gross price change**, not net ROI or fill performance. Retention of old local rows is not guaranteed. Avoid uploading capture JSON to public issues: it may reveal local source metadata.

## Acceptance boundary

- **Source/test/CI:** prove actual current M04 input can be saved and re-evaluated without a second historical selection, forged role/digest, source path mismatch, world-readable file, symlink, lookahead, trade creation or provider call.
- **VPS evidence pending:** owner runs capture of a natural 1–2 candidate WATCH, records outcome after the relevant horizons and verifies M03/M05/BTC remain active. No guarantee all horizons have samples.
- **Still OPEN:** actual MARKET/RISK/STRATEGY responses, verified G1, quantified executable spread/fees/depth, M05 MANUAL owner callback, PAPER entry/position lifecycle, exchange fills/settled IDR and LIVE release. Preserve NO/NO/DRY_RUN.

## M09 natural WATCH coverage diagnostics (independent read-only operator)

Run this *additional* analysis on the **same** owner-only (0600) capture and
the **same** original source DB after (or during) any horizon; it does not
recapture M04 or run another scanner. Example with the original 23 November
TMG/DRX observation:

~~~bash
(
  set -euo pipefail
  cd "$HOME/indodax-ai-bot-v2"
  export INDODAX_TRADING_ENABLED=NO
  export INDODAX_ALLOW_LIVE_EXECUTION=NO
  export INDODAX_EXECUTION_MODE=DRY_RUN

  .venv/bin/python -B -m scripts.m09_ws3_watch_coverage_audit_readonly \
    --source-db "$PWD/runtime/decisions.sqlite3" \
    --audit-capture \
    "$PWD/runtime/m09_ws3_watch_capture_VlyEYT38.json"
)
~~~

The JSON attaches each candidate to its original M04 evidence digest and
shows, per 5/15/30/60m horizon:

- Original M09 outcome, including observed *gross* price percentage or null.
- Local price receipts inside the original horizon [target,target+30s],
  known by the evaluation time.
- Nearby receipts within ±120 seconds, also limited to data received by
  evaluation time. These are **diagnostic only**, never substitute for a
  missing exact-horizon price.
- A separate classification for on-target observed, before horizon, window
  still open, invalid on-target price, nearby-only, and no nearby receipts.
- Ratio of observed windows only among horizons whose 30-second exchange
  window is closed. A late-arriving indexed sample can still update the
  research result later. A missing window never becomes a 0% return.

This helps isolate **where** local coverage is missing but cannot determine
**why**: no conclusion about exchange inactivity, a broken collector, full
tape completeness, liquidity, or stale orderbook can be drawn just from these
counts. It does not change M04 WAIT, call paid MARKET/RISK/STRATEGY, or
authorize a PAPER/real fill. Original M09 PAPER lifecycle simulations remain
separate until a genuine qualified review and owner-authorized source
conditions exist.

### First natural VPS evidence, 23 November 2026

- DRX/IDR captured WATCH 11:37:03 WIB at IDR 23.576866. Five-minute
  local exchange timestamp 11:42:22, receipt 11:42:23 (1-second
  receipt lag), observed IDR 23.647275, gross +0.2986359595%.
- TMG/IDR baseline IDR 14; no 5-minute horizon receipt in the original
  lookup window, UNKNOWN rather than a losing trade.
- At the 15-minute check, neither pair had an on-target local price.
  The 30/60-minute horizons were not yet mature at that check.

These are **observational** results only. No buy, provider response, PAPER
fill, net ROI or G1 proof is recorded. Do not publish private capture JSON or
claim all horizon coverage from this evidence.
