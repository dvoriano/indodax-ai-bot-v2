# M03 72H Micro → M05 prospective ACK evidence | Original V2 | 12 Nov 2026

**Scope:** original owner VPS `~/indodax-ai-bot-v2`, source `main` commit `5e788f8f830ba1ca62dd9e25fd2f48cdc19e84c0`. Owner supplied terminal outputs, not GitHub-CI reproduction or a fresh connector inspection of the VPS. All reported audit commands were **read-only**, no forced M03 tick, no M05/BTC restart, no Telegram test-send, no AI provider calls, and no exchange orders. Existing original M05 PID `588864` was stable in the checked invocations. Keep execution `NO/NO/DRY_RUN`.

## Owner VPS evidence accepted, bounded

1. Owner updated clean original `main` to pinned SHA `5e788f8` by fast-forward, and **30/30 scoped offline tests PASS**. M03 private EnvironmentFile preflight was ready, then separate owner-GO research-only drop-in enabled `M03_WS3_MICRO_72H_ARCHIVE_GO=YES` with `M03_WS3_EVIDENCE_ARCHIVE_GO=YES`. Read-only postreload preflight `M03_MICRO_72H_ENABLED_CONFIG_READY_NO_START` passed; normal M03 timer stayed active and original M05 PID did not change. Owner did not manually start/stop/restart M03/M05.
2. Following **natural** M03 cycles, original private 72h `observations.sqlite3` contained 14 micro research rows over 7 consecutive minutes (15:18–15:24 WIB), 2 pairs/minute, 10/14 with fresh trade timestamps, 14/14 with book timestamps, zero observed gaps/truncations/malformed or authority flag violations in this bounded window. M03 private source cycles count observed: 1,308. **Not 72 hours of proven continuous data.**
3. Owner fixed 30-row research cohort **15:42–15:56 WIB**: 17/30 same-minute price joins, **29/30** prior-or-same-minute source price fresh `<=150s` at micro capture, **18/30** fresh trade, **30/30** fresh book, and **17/30** simultaneously fresh price+trade+book. In 13 unmatched same-minute rows, 12/13 were previously priced and fresh as of micro, while **UAI/IDR 15:44 had source-price age 158s** against 150s limit. Preserve `STALE`, do not stretch threshold or call this G1.
4. Owner queried original `m05_radar_delivery_events` after 15:18 WIB and obtained **11 original WS3 `ACKED`** claims at 15:21:17–16:05:05 WIB for **6 unique pairs**: TMG (3), ANKR (3), NBTNEW (2), ANOA (1), CST (1), ONL (1). All Bot API acknowledgement delays from claim to ACK were 0–1s, **not source-to-delivery latency or device reads**.
5. Exact 11-ACK cohort retrospective as-of micro lookup from private research archive (bounded to 180s before each claim) classified **7/11 `NO_PRECLAIM_MICRO_WITHIN_180S`** and **4/11** with fresh trade and book prior to claim. Two directly classified in the first audit: **ONL 15:52:06 (micro age 75s)** and **ANKR 16:02:19 (age 88s)**. Two originally isolated as `MICRO_AFTER_ALERT_SOURCE` were independently rechecked: **NBTNEW 15:34:21 (age 30s)** and **CST 15:46:06 (age 15s)**, both `FRESH_CAPTURE=True`, `FRESH_TRADE=True`, `FRESH_BOOK=True`, and captured **after the original alert price-source timestamp but before the claim**. This timestamp order is not a post-claim observation. It is also **not** proof that the original sender evaluated the micro evidence.
6. All four positive micro observations are a **bounded retrospective proof of available observed data before claim**, **not** complete joined as-of price+trade+book for each ACK, nor a confirmed live sender dependency. Seven missing rows are **UNKNOWN / sampling coverage**, never zero trades or failed signals.

## Code path and risk interpretation

At pinned source the original `run_m03_ws3_summary_watch_once` chooses **up to two** micro pairs per one existing WS3 session based on earlier M03 price observations (plus bounded rotation); M05 can emit observational owner WATCH from fresh WS3 price/turnover and uses micro primarily for review metadata/ranking. It does **not** require a verified micro match to publish WATCH. The new 72h research archive reuses that same collected snapshot; no added socket or exchange requests. Source-side BUY/SELL labels are **not independently verified taker aggressor**. Thus 4/11 should **not** be called a win rate, strategy precision/recall, expected profit or AI/trading readiness; 7/11 absence does not prove missing market activity.

**Operational handling:**
- Preserve early WATCH publication, existing alert thresholds, M03 two-pair source budget and existing service continuity.
- Distinguish `NO_MICRO_OBSERVED`, `BOOK_ONLY`, `TRADE_BOOK_OBSERVED` and stale/source-timeline cases in **future nonexecuting evaluation**, using claim-time source cutoff and immutable historical frames.
- Do not classify post-alert-price-source observations as available at source timestamp. Separate **market source epoch**, **micro captured epoch**, **M05 claim epoch**, and **Telegram ACK epoch**.
- Before risk or trading planning, insist on independently validated as-of price, trade and book plus documented source limits; keep G1 issuer/completeness and M04 actual provider role reply acceptance OPEN.
- Prefer **one original read-only/reusable as-of audit** and future prospective natural ACK evidence over repeated owner shell one-off scans. Any code change must pass targeted offline tests and CI before optional owner VPS source sync, with **no runtime activation inferred from source-only tests**.

## Master ledger placement and acceptance

This is **evidence register support** for the original open M03 OOS/source-quality, M04 real source/role response, and M05 proposal/owner gating acceptance items. It does not add checklist rows, change checkbox states, authorize paid models, update filters, restart services or approve a live trade.

**Master remains 52/98 DONE and 46/98 OPEN.** Original `docs/ROADMAP.md` is the one master ledger. 
