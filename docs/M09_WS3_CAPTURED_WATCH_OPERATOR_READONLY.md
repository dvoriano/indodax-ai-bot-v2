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

## M04 retained pre-capture price coverage (separate diagnostic, READ ONLY)

The first natural TMG/DRX 23 November 2026 capture is **already saved**.
For *that same* private 0600 original WATCH, the optional research-only tool
measures **retained** local prices with both `exchange_epoch <= captured_epoch`
and `recorded_epoch <= captured_epoch`. It reports 5m/15m counts,
latest observed age, largest observed *inter-sample* gap, and worst receipt
lag. All are source-availability diagnostics, not evidence of liquidity or
MARKET/RISK/STRATEGY qualification.

~~~bash
cd "$HOME/indodax-ai-bot-v2"
export INDODAX_TRADING_ENABLED=NO
export INDODAX_ALLOW_LIVE_EXECUTION=NO
export INDODAX_EXECUTION_MODE=DRY_RUN

.venv/bin/python -B -m scripts.m04_ws3_precapture_coverage_audit_readonly \
  --source-db "$PWD/runtime/decisions.sqlite3" \
  --audit-capture \
  "$PWD/runtime/m09_ws3_watch_capture_VlyEYT38.json"
~~~

**Critical retrospective limitation:** existing local price index uses SQLite
upsert and pruning. Its current rows with recorded timestamps <= capture
are NOT cryptographic, immutable proof of the complete SQLite state at
the earlier historical capture. An overwritten historical row could no
longer be present. Therefore this is correctly labeled
`RETAINED_PRECAPTURE_SOURCE_RESEARCH_ONLY`, not independently verified
*as-of* coverage. Future at-WATCH evidence needs to be captured
contemporaneously if it will ever be used for a real decision. The
existing M04 WATCH selection, digests, and 3 uncalled role inputs are
preserved, not reselected. No coverage threshold or trading gate is
created; zero rows means no retained rows, **not** zero price movement.
Do not call this an AI win-rate, fill model, G1 issuer, execution
eligibility check, or complete upstream WS3 coverage.

## Compare original WS3 UNKNOWN with official INDODAX 1m public OHLCV (optional, operator only)

The official INDODAX Public REST API documents a 1-minute history endpoint:

- `GET https://indodax.com/tradingview/history_v2?from=<unix>&symbol=BTCIDR&tf=1&to=<unix>`
- Required request fields: `from`, `to`, `symbol`, `tf`; response uses `Time`, `Open`, `High`, `Low`, `Close`, `Volume`.
- Source: https://github.com/btcid/indodax-official-api-docs/blob/master/Public-RestAPI.md#ohlc-history

The separate script `scripts/m09_indodax_public_candle_reference_readonly.py` **never runs in M03, M04, Telegram, or M09 PAPER runtime**. Its default is **NO NETWORK**. It only issues a maximum of two bounded unauthenticated public HTTPS GET requests when the operator explicitly supplies `--fetch-public-ohlcv`. It validates the same owner-only original M04 capture, uses only the official fixed INDODAX host, strips raw API payloads from stdout, caps body, bars and time span, and needs **no API key**.

~~~bash
(
  set -euo pipefail
  cd "$HOME/indodax-ai-bot-v2"
  export INDODAX_TRADING_ENABLED=NO
  export INDODAX_ALLOW_LIVE_EXECUTION=NO
  export INDODAX_EXECUTION_MODE=DRY_RUN
  .venv/bin/python -B -m unittest -q     tests.test_m09_ws3_watch_price_outcomes_readonly     tests.test_m09_04_original_virtual_paper     tests.test_m04_ws3_indexed_observation_nonlive
  .venv/bin/python -B -m scripts.m09_indodax_public_candle_reference_readonly     --source-db "$PWD/runtime/decisions.sqlite3"     --audit-capture "$PWD/runtime/m09_ws3_watch_capture_VlyEYT38.json"     --fetch-public-ohlcv
)
~~~

If the VPS public REST is blocked, the report will state `PUBLIC_CANDLES_UNAVAILABLE_OR_INVALID` for that pair; **do not fabricate candle values** or bypass security policy. It is not a reason to restart M03. If Indodax does not return TMGIDR/DRXIDR bars for those times, each missing horizon is `CANDLE_MINUTE_NOT_RETURNED`; other pairs' candles must not substitute.

For a given target second, the script selects the **closed 1-minute bar containing that target second**, with `candle_minute_open_epoch = floor(target / 60) * 60` and `candle_minute_close_epoch = open + 60`. A candle is reference-observed **only** if the entire minute has closed by `evaluation_epoch`, OHLCV is valid, volume is strictly positive and INDODAX actually returned that exact minute. A zero-volume bar is `CANDLE_ZERO_VOLUME_UNVERIFIED`, never a confirmed trade. The close may occur up to 59 seconds after the exact M09 target second; it is **not an observed price at that second**.

The report preserves `ws3_original_outcome_status` and original 30-second `coverage_class`. It reports a separate `CLOSED_1M_CANDLE_REFERENCE_OBSERVED` and a separately named **gross candle-close proxy change from original M04 WS3 baseline**, NOT official 5m/15m/30m/60m WS3 outcome, fill, net ROI, win rate or G1/AI proof. A later public response also does not prove that an historical candle was immutable at its original close. No modifications to exchange credentials, public WS, original M03/M04 ranking, order adapter, timers or running services.

## Natural INDODAX 1m candle check: all returned, seven volume-zero (23 November 2026)

Original VPS operator updated to `c3c4b43d` and ran 52/52 focused regression PASS. The official public endpoint returned two valid bounded API responses for TMGIDR and DRXIDR. Eight target 1m candles were present, but **seven reported `Volume = 0`**. TMG: four of four zero-volume. DRX: 5m positive Volume 25372, price Close 23.647275 matching one observed WS3 5m, while its 15m/30m/60m candles reported zero volume. Local WS3 outcome was still 1 observed out of 8 and seven UNKNOWN, `execution_allowed=False`.

Official INDODAX documentation explicitly includes a valid OHLC example with `Volume: "0"`, so a present OHLC candle is **not proof of a trade in that minute**. Minute volume and rolling 24h volume have distinct windows. It is not yet proven whether the seven zero-volume responses represent no matched trades in those minutes versus exchange-history placeholder/aggregation semantics. Never silently turn `Volume=0` into a trade, rank, price at target second, paper fill or net-profit estimate.

A follow-on diagnostic exposes `candle_zero_volume_reference_close_idr` and its separately named proxy change for zero-volume candles, retaining `CANDLE_ZERO_VOLUME_UNVERIFIED`, `candle_close_idr=None` in the trade-observed slot, and the untouched WS3 `UNKNOWN`. It counts `candle_minutes_present` and `candle_zero_volume_references` independently of `candle_references_observed`, making source delivery visible without claiming price execution. To confirm actual trade activity in a zero-volume minute, use original timestamped trade-tape evidence with completeness/gap checks (independent G1 remains OPEN), not rolling 24h turnover or OHLC alone.

## Available candle prices are NOT UNKNOWN in the unified historical reference report

The original WS3 outcome evaluator remains byte-for-byte unchanged: original TMG/DRX
capture had 1/8 on-target WS3 observations and seven `UNKNOWN` WS3-specific
statuses. This is a **WS3 source coverage measurement**, not evidence that
Indodax's historical candles are unavailable.

The public candle research report now also exposes a **distinct price
availability layer**. If a fully closed 1-minute Indodax candle is present
and valid, `price_reference_status=PRICE_REFERENCE_AVAILABLE`,
`price_reference_close_idr` and
`price_reference_gross_change_from_ws3_baseline_percent` are always
populated, **including when the official Volume is 0**. The last is a
close-of-target-minute gross change relative to original M04 baseline,
not a price quotation at the exact target second, not a strategy outcome,
not net executable ROI. `price_reference_volume_zero` reports the
source volume condition. The per-pair field `candle_status` continues
to say `CANDLE_ZERO_VOLUME_UNVERIFIED` for zero-volume candles to avoid
pretending a matched trade was independently proven.

At the report level:
- `price_reference_available_horizons` counts validated closed candles regardless of Volume
- `price_reference_unknown_horizons` counts closed target minutes whose candle price could **not** be validated/retrieved
- `price_reference_pending_horizons` counts target-minute candles not yet closed
- `price_reference_coverage_percent` counts available / (available + unknown) closed bars, which **must not** be conflated with original WS3 30s coverage, liquidity or fill reliability

For the actual TMG/DRX VPS source response received 23 November 2026, **all eight
target-minute OHLCV rows were returned and parsed, including seven Volume=0**.
Therefore the available *candle price reference* count is expected to be
**8/8** when that same response remains available; seven WS3 unknowns remain
**7/8** as the result of that specific WS3 evaluation method. Price is not
missing, but accurate target-second trade confirmation is missing.

All API calls are opt-in public GETs; no runtime worker, no original M04
candidate/ranking change, no automatic order, and no inference of PAPER
fills/profits. The original M09 source index has finite retention, so
the same retrospective evaluation may produce a changed WS3 diagnostic
if its underlying rows have expired. This report is not a deterministic
historical trading replay.

## M04 natural WATCH versus M03 trade-channel requests (operator research)

The independent script `scripts/m04_ws3_watch_channel_coverage_readonly.py`
correlates **original M04 WATCH candidates and their original digest** with
recent M03 continuous checkpoints and retained local trade-index rows. It does
not launch a scanner, open a WS/REST connection, modify a database, send
Telegram, call a paid model or create PAPER/exchange orders.

Use the **original** saved 0600 capture for a retrospective investigation:

```bash
(
  set -euo pipefail
  cd "$HOME/indodax-ai-bot-v2"
  export INDODAX_TRADING_ENABLED=NO
  export INDODAX_ALLOW_LIVE_EXECUTION=NO
  export INDODAX_EXECUTION_MODE=DRY_RUN
  .venv/bin/python -B -m scripts.m04_ws3_watch_channel_coverage_readonly \
    --source-db "$PWD/runtime/decisions.sqlite3" \
    --audit-capture "$PWD/runtime/m09_ws3_watch_capture_VlyEYT38.json"
)
```

The prior capture is *retrospective*: although the original capture is validated
by the existing M09 validator, the current local SQLite index is mutable and
pruned (24h trade-index retention). The script enforces `recorded_epoch <=
captured_epoch` and `exchange_epoch <= captured_epoch` on retained rows and
`observed_epoch <= captured_epoch` on checkpoints, but cannot prove that
the retained source was **immutable at past WATCH time**. Journal gaps can
also reflect log retention, not exchange inactivity.

To collect a *new* natural WATCH's evidence without reconstructing M04
candidate selection from hindsight, run this **only when an original M04
WATCH is actually current** and save its sanitized output as a local private
0600 file:

```bash
(
  set -euo pipefail
  cd "$HOME/indodax-ai-bot-v2"
  export INDODAX_TRADING_ENABLED=NO
  export INDODAX_ALLOW_LIVE_EXECUTION=NO
  export INDODAX_EXECUTION_MODE=DRY_RUN
  umask 077
  .venv/bin/python -B -m scripts.m04_ws3_watch_channel_coverage_readonly \
    --source-db "$PWD/runtime/decisions.sqlite3" \
    --capture-current > "$PWD/runtime/m04_watch_channel_coverage_$(date +%s).json"
)
```

A valid capture has status
`M04_WATCH_TRADE_CHANNEL_COVERAGE_RESEARCH_ONLY`; otherwise no eligible
fresh original M04 WATCH was available or required source evidence could not be
validated. This command writes **only its explicitly redirected output file**.
It does not change original trading services. Inspect the result and retain
its mode 0600 before using it as operator research evidence.

Read result fields carefully:

- `checkpoint_request_membership`: true means the pair appeared in the
  latest recorded checkpoint `indexed_trade_pairs` (within 120s before
  capture). False means not listed **in that checkpoint**. Null means no
  valid recent checkpoint. All are *request-membership observations*, **not**
  a direct pair-specific subscription ACK or proof of full-minute coverage.
- `pair_specific_ack_evidence=NOT_AVAILABLE_IN_CHECKPOINT` is deliberately
  explicit. Current source `ws_stream_state` has a per-pair cursor status,
  but lacks the timestamped ACK provenance needed to attest active
  subscription at the WATCH instant. On saved retrospective captures even
  this mutable cursor status is omitted.
- `source_local_retained_trade_windows` covers 1m, 15m and 60m **before
  capture**, excluding receipts recorded after capture. Count zero does
  **not** mean the exchange had no trades.
- `local_trade_publications_60m_by_receipt` counts local receipt epochs,
  whereas trade sample counts use exchange epochs. They are not equivalent.
- `recorded_discontinuities_60m=0` means zero *recorded* gaps, not verified
  continuity. `pair_specific_ack_verified=False`,
  `historical_source_immutability_verified=False`,
  `independent_g1_verified=False`, and `execution_allowed=False`
  are intentionally preserved.

This prototype addresses the TMG (requested-index membership, 14 local trades
in the earlier ~62m) versus DRX (not selected for indexing in four target
minutes, no local trades) discrepancy without changing the 40-channel
allocator. Formal per-pair subscription ACK and complete 15m/1h trade tape
will require additional independently verifiable capture-time metadata;
the existing checkpoint is not sufficient. Meanwhile the Indodax OHLCV
reference Close remains **8/8 available** on the original TMG/DRX cohort.
