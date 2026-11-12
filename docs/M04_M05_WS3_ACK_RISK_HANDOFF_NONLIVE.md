# Original M03→M05 ACK→M04 three-role input handoff (research only)

This scoped feature augments the existing original-V2 **read-only** M05 WS3 ACK/source-quality inspection. It does not alter the M03 public collector, Telegram sender, three-role model provider, trading permission or risk preflight.

## Factual source lineage

- Input: original M05 Bot API ACK ledger (pair, source exchange epoch, claim epoch), exact original private M03 WS3 source price evidence (as-of claim), and bounded original M03 72h micro research archive (as-of claim). Upstream source join is already implemented by the original m05_ws3_asof_quality_nonlive module.
- Integration: audit_ws3_ack_cohort_nonlive now passes each **validated as-of quality** into prepare_m04_m05_ack_research_role_inputs_nonlive. It returns a compact M04 handoff under each event, with a source-only SHA256 digest, ordered original canonical role **INPUT slots** MARKET, RISK, STRATEGY, deterministic caution/reject reasons, and strategy WATCH_NO_TRADE. One exact source digest is reused for all three uncalled role input slots. Digest is a local integrity fingerprint, **never an independent G1 issuer receipt**.
- Tiers SOURCE_UNVERIFIED, PRICE_ONLY, BOOK_ONLY, TRADE_ONLY, TRADE_BOOK_OBSERVED retain their prior 150s source and 90s trade/book/capture validity conditions. Unknown microstructure is never fabricated as zero buying/selling.
- A micro snapshot captured **after the price-source epoch but before claim** is identified as such and gets MICRO_NOT_AVAILABLE_AT_ALERT_SOURCE_EPOCH risk caution. It cannot be misrepresented as proof that the original M05 sender consumed it when detecting momentum.
- Other cautions include observed spread over 50bps, top-five bid depth below Rp1m and source-reported SELL-side concentration. These are **uncalibrated research flags** and not verified taker aggressor flow or profit/loss labels.

## Authority boundary

The original provider and real MARKET/RISK/STRATEGY models remain **uncalled**. The deterministic RISK veto is not an actual AI response. Every packet remains:

- independent_g1_issuer_verified = false; full trade tape and closed-candle provenance unverified;
- actual_three_model_responses_observed = false; paid_ai_owner_go_authorized = false;
- final_decision = REJECT, execution_allowed = false, orders_sent = 0, telegram_messages_sent = 0;
- no original M05 alert filters, service/PID, systemd unit, M03 timer or REST/WS request changes.

This feature is an observability handoff, **not** a BUY proposal, net-return calibration, AI dispatch or a trader control.

## Original owner VPS follow-up

After pinned source code update and targeted offline tests, call the **same existing** read-only original-V2 CLI:

~~~bash
cd "$HOME/indodax-ai-bot-v2"
.venv/bin/python -B -m scripts.m05_ws3_asof_quality_readonly \
  --start-wib 2026-11-12T15:18:00 \
  --end-wib 2026-11-12T16:05:05
~~~

Expected bounded real-cohort output is the existing evidence-tier profile (previously 11 price-fresh ACKs, 4 partial trade+book, 7 price-only) with new per-ACK m04_research_handoff fields and aggregate m04_uncalled_role_input_slots_prepared. Nothing from this source-only change authorizes an AI provider request, additional alert, restart or an exchange order.

The existing M04 G1 independent issuer, full trade tape, genuine paid role responses and one real order-specific owner proposal remain **OPEN** on the sole master docs/ROADMAP.md. Master count remains **52 DONE / 46 OPEN**.
