# M05 WS3 As-of Source + Micro Quality | Original V2 (nonlive)

Purpose: reconstruct source-quality metadata for **real original M05 owner Bot API ACKs**, using the original M03 private 72-hour WS3 price/micro research SQLite. This is **not** an AI model, G1 verification, a trading system, or a replacement for the original M03/M05 caller.

## Timeline and coverage contract

- Read the existing owner-private M05 delivery ledger and original M03 research archive in **read-only** SQLite mode, without new collectors or exchange/network calls. Running original M05 PID must remain stable and its read-only guards plus priority flag must be verified.
- Filter at most 64 actual owner events, inside an explicit <=24-hour interval within the archive's 72-hour *maximum retention*. Only acknowledged WS3 events are reconstructed; REST and pending events are identified separately. Empty or missing archives are never imputed as market silence.
- Join the original ACK to the archived WS3 price source on **pair plus exact original source exchange epoch**, only if its local research cycle was recorded by ACK claim time. The original 150-second M05 source-freshness limit, quote structure and minimum rolling 24-hour turnover Rp150m remain enforced.
- Micro research may be available in the 180 seconds before claim. A valid capture has local age <=90s; individual trade/book stamps must independently be <=90s, with consistent order-book bid/ask/spread and no malformed, truncated or gapped source flags.
- Classify each ACK as SOURCE_UNVERIFIED, PRICE_ONLY, BOOK_ONLY, TRADE_ONLY, or TRADE_BOOK_OBSERVED. Always include micro freshness and the micro-versus-alert-source chronology. A micro snapshot **after the price-source timestamp but before claim** can be observed, yet is **not proof** that the original M05 sender consumed it or that it was available when price movement first began.
- All tiers preserve G1=false, risk veto=true, final decision REJECT, execution=false. Source BUY/SELL side remains unverified aggressor data. The research cautions on >50bps spread, <Rp1m bid depth and source-reported SELL concentration are **not net-edge calibrated**.

## Safe original VPS command (only after code is present)

~~~bash
cd "$HOME/indodax-ai-bot-v2"
.venv/bin/python -B -m scripts.m05_ws3_asof_quality_readonly \
  --start-wib 2026-11-12T15:18:00 \
  --end-wib 2026-11-12T16:05:05
~~~

Use another actual owner-supplied WIB time range for future prospective audits. This operation does **not** restart M05, force M03 ticks, modify databases, issue a Telegram send, call a paid AI model, or place BUY/SELL/CANCEL orders. No owner ID, Bot API token, message ID, private file path or raw env is printed.

## Acceptance and checklist

The 12 November owner audit already observed **11/11 original WS3 Bot API ACKs**, **4/11 partial fresh preclaim trade+book captures**, and **7/11 with no 180-second preclaim micro record**. Those historical figures are **not** test fixtures proving an original source join in the new code, complete G1, actual live model responses, realized net PnL, or a positive hit rate. The owner must test this new source on original VPS before runtime acceptance.

This is a source-quality research step underneath the **existing OPEN** M03/M04/M05 checklist items in the sole master ledger docs/ROADMAP.md. No new checkbox, no trading permission; master remains **52/98 DONE / 46/98 OPEN**.
