# M05 WS3 Post-ACK Signal Edge Audit

## Purpose and limits

This is a READ-ONLY retrospective audit, **not** an automated trading strategy, backtest of executable orders, or profit claim. It reuses:

- The existing chronological M03 WS3 / M04 MICRO PREALERT / M05 TOP5 quality replay.
- The original owner-private M05 durable delivery-event ledger.
- Retained WS3 last-price observations **after** the real Bot API ACK.

Four distinct timing layers must not be confused:

1. First retained public quote: not the first market opportunity or listing.
2. First replayed M04 micro PREALERT / M03 WATCH / M05 eligible / M04 quality (minute+59, as-of): not when the original process fired.
3. Original M05 owner claim then matched Bot API ACK: a durable per-pair receipt, not evidence of the owner's phone receiving/displaying the notice.
4. Ex-post later last-price quotes: **not executable bid/ask, a fill, or actual net profit**.

## Running on the original V2 VPS

Only use after the PR has passed CI and the code has been merged and synced. Do not restart M03, WebSocket or Telegram services just to run this report.

The command below obtains the owner-private DB path from the already running M05 service environment; it does not print the path, token, or owner ID. It explicitly checks NO/NO/DRY_RUN first.

    (
      set -euo pipefail
      cd "$HOME/indodax-ai-bot-v2"
      .venv/bin/python -B - <<'PY'
    import json
    import os
    import subprocess
    from datetime import datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo
    from scripts.m05_signal_edge_audit_readonly import inspect_postack_quality_readonly

    pid = int(subprocess.check_output(
        ["systemctl","--user","show","indodax-v2-m05-telegram.service",
         "-p","MainPID","--value"], text=True).strip())
    if pid <= 0:
        raise SystemExit("M05_NOT_RUNNING")
    env = dict(x.split(b"=", 1) for x in
               Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
               if b"=" in x)
    required = {
        b"INDODAX_TRADING_ENABLED": b"NO",
        b"INDODAX_ALLOW_LIVE_EXECUTION": b"NO",
        b"INDODAX_EXECUTION_MODE": b"DRY_RUN",
    }
    if any(env.get(k) != v for k, v in required.items()):
        raise SystemExit("UNSAFE_EXECUTION_FLAGS")
    ledger = Path(os.fsdecode(env[b"M05_TELEGRAM_OWNER_DB"]))
    wib = ZoneInfo("Asia/Jakarta")
    def at(clock):
        return int(datetime.fromisoformat(
            "2026-11-11T" + clock).replace(tzinfo=wib).timestamp())
    result = inspect_postack_quality_readonly(
        start_epoch=at("11:45:00"),
        end_epoch=at("11:53:00"),
        pairs=("onl_idr","bnb_idr","anoa_idr"),
        radar_claims_db=ledger,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    PY
    )

Optional: supply both fee_bps="30" and slippage_bps="20" to the same Python function to subtract a hypothetical **total round-trip** 50 basis-point cost from **price-only** returns (1 bp = 0.01%). **Neither fee nor slippage has a default**. This scenario is NOT realized net PnL: bid/ask spread, actual fills, order-book depth, trade tape and slippage remain unverified.

## Interpreting statuses

- MATCHED_WS3_ACK: ACK is correlated to an exact retained WS3 source exchange timestamp.
- ACK_NOT_MATCHED_WS3_SOURCE: REST provenance or source match absent. Do not infer WS3 delivery or outcomes.
- NO_CONFIRMED_ACK: source may be CLAIMED or UNCERTAIN, which is not Bot API ACK.
- HORIZON_NOT_MATURE: no evaluation because the clock has not passed the price horizon.
- NO_FRESH_POST_ACK_ENTRY_QUOTE: no retained quote 0–90 seconds after ACK. This does not mean a loss.
- NO_FRESH_HORIZON_QUOTE: no retained quote 0–90 seconds after ACK+5m or ACK+15m. This does not mean a loss.
- OBSERVED_PRICE_ONLY_NOT_EXECUTABLE: post-ACK and horizon last-price snapshots exist. The change is an ex-post indicator only.

No actual market order, exchange API call, Telegram send, paid AI call, live execution or service change is made. Both original SQLite files must be owned, private regular files and are accessed read-only. Maximum scope: 10 pairs, 120-minute event window, 500 owner events.

## Grounded baseline and remaining gaps

The owner-provided original V2 real read-only evidence from 11 November 2026 records three matched post-cutover WS3 ACKs: ANOA source→ACK 33s, BNB 44s and ONL 31s. These are not earliest opportunity→Telegram alert or measured live-trading profits. Original UAI pre-deploy per-pair ACK history cannot be reconstructed.

Even a positive 5m/15m ex-post last-price proxy does not establish an out-of-sample tradable edge. Realized fills, executable spread/depth, fees/slippage, negative controls and calibrated precision remain open. Keep the master checklist unchanged until independently proven.

## Historical exact-epoch recovery from separate 72h research evidence (explicit opt-in)

Owner evidence (12 Nov 2026): ten 11 Nov 23:10–23:37 WIB WS3 owner-private Bot API `ACKED` events (UAI, CST, NBTNEW, ERN, TMG, ONDO) were all reported `ACK_NOT_MATCHED_WS3_SOURCE` by the initial read-only audit several hours later. Inspection of the **actual** `inspect_postack_quality_readonly` source confirms it only joins `m03_ws_summary_watch_minute`, which prunes at roughly **2h**. Therefore a missing historical quote is **not evidence of a fake/not-sent ACK, a bad signal or a loss**. Historical M04 micro/timeline replay also needs that original 2h table, and cannot be recreated from the new research archive.

The same original M03 now independently stores some source snapshots in the **private opt-in 72h maximum** archive `~/.local/state/m03-ws3-evidence/observations.sqlite3`, with a **250,000-row hard cap** and partial-pair WS3 coverage. Rows store `pair`, actual `source_exchange_epoch`, `last_idr`, rolling-24h turnover, source eligibility and local record time in companion cycle table. No trades, order-book fills or full-market history are proven.

The offline analysis tool now accepts **optional** `research_evidence_db=Path("...")` (or CLI `--research-evidence-db`), **never automatic**. It checks a UID-owned 0600 regular file in a UID-owned 0700 directory, opens it read-only and reads bounded original research rows. A historical ACK can get `MATCHED_WS3_ACK_RESEARCH_ARCHIVE` only when the recorded snapshot shows **the exact same pair and source exchange epoch**, source status was `ELIGIBLE_SOURCE_OBSERVATION` and the original M03 archive actually recorded that observation **no later than the M05 claim**. It reports `source_match_provenance=M03_WS3_PRIVATE_RESEARCH_ARCHIVE` distinctly from `ORIGINAL_M03_WS3_MINUTE_DB`. An absent, disqualified, post-claim, invalid or conflicting quote **does not match**, even when the Telegram Bot API already ACKed a message. Historical missing M04 micro PREALERT/eligibility stages stay `None`, never reconstructed or zeroed.

For a historical investigation, use the running original M05 owner environment's private ledger path and call the already-installed audit function with the extra argument `research_evidence_db=Path.home() / ".local/state/m03-ws3-evidence/observations.sqlite3"`. The original source DB argument remains the same. The latest quote-only 5m/15m proxies now may use the explicit archive's retained **observational last-price** rows, including below-floor later prices, when fresh at each horizon. They are **never** trades, executable prices, Risk ALLOW, or net PnL. Requiring two known fees/slippage inputs for hypothetical cost scenarios is unchanged. Do **not** substitute these last-price snapshots for M04 role PREALERT evidence. Because the archive itself is bounded, all missing coverage remains UNKNOWN rather than interpreted as a negative outcome.

No market request, owner Telegram send, AI/provider call, extra scanner/socket, database mutation, change to the original 2h M03 archive, scheduler, M05 cooldown, or BUY/SELL/CANCEL is added. The optional evidence path must be explicitly provided. The new function must be CI and owner-VPS verified before treating any recovered pair results as factual.

## Paired pre-source context: first UAI follow-through vs later re-alert (12 Nov 2026)

Owner read-only original M05 ACK ledger and original M03 private 72h public-source snapshots for 11 Nov 2026 **23:10–23:37 WIB** showed 13 WS3 ACK events in an ad-hoc, unfiltered query, of which **10** were the intended six-pair `UAI,CST,NBTNEW,ERN,TMG,ONDO` cohort and **3 additional** `NEON,ONL,UCJL` were outside that intended scope. These three ad-hoc `SOURCE_PRICE=UNVERIFIED` outputs came from a Python pair-filter mismatch and must **not** count against the earlier 10/10 exact source matched cohort.

Public last-price changes **BEFORE** the matching source quote, using an as-of owner claim snapshot and an observed baseline within the preceding 90 seconds of each 5m/15m target, were:

- UAI 23:22:10 ACK, **Rp655 source**, pre5 **−0.91%**, pre15 **−0.91%**; independent ex-post last-price +2.82% over 5m and +2.37% over 15m.
- UAI 23:37:18 ACK, **Rp696 source**, pre5 **+0.43%**, pre15 **+6.91%**; ex-post +1.01% over 5m and +0.58% over 15m.
- ERN 23:12 ACK, pre5 −2.83%, pre15 unavailable; ex-post 5m −0.73%. ERN 23:34 ACK pre5 +0.98%, pre15 −0.36%, ex-post 5m −1.47%.
- TMG 23:17 ACK pre5 +3.12%, pre15 0.00%, ex-post 5m −2.94%; TMG 23:33 ACK pre5 +3.03%, pre15 +3.03%, ex-post 5m +2.86%.
- NBTNEW 23:18 ACK pre5 −8.77%, pre15 0.00%, ex-post 5m unavailable; NBTNEW 23:36 ACK pre5 −1.62%, pre15 −3.75%, ex-post 5m unavailable.
- CST 23:19 ACK pre5 unavailable, pre15 +0.24%, ex-post 5m −0.70%. ONDO 23:28 ACK pre5 +0.74%, pre15 +0.75%, ex-post 5m unavailable.

These are sparse public **last-price** quote proxies, not candles or actual fills, and no claims about trade-side pressure/order-book activity can be inferred. The first UAI ACK has favorable observed follow-through without a preceding 5m rise; the second has already experienced a substantial **15m** preceding rise and smaller follow-through, making it useful to study for delayed alerts. This is a two-event **hypothesis**, not proof of first market entry timing, profitable net scalping or validated M04 MICRO PREALERT/three AI role outputs. UAI first source/ACK time and later source/ACK time are real, but the earliest market opportunity remains unknown. The source-to-ACK gap is not the Telegram API call duration.

### Canonical bounded implementation

Rather than repeatedly using a freehand script, the existing audit function now provides `pre_source_price_context["5m"|"15m"]` for explicitly requested research archive evidence only. It uses the exact already matched `source_observed_epoch`, finds a baseline public quote at most 90 seconds before the *pre-source* horizon, only uses original private research rows actually recorded no later than original M05 claim, and refuses ambiguous prices. Each status distinguishes `OBSERVED_PRE_CLAIM_LAST_PRICE_ONLY`, `NO_FRESH_PRE_SOURCE_QUOTE`, `SOURCE_PRICE_NOT_VERIFIED_IN_RESEARCH`, or `CONFLICTING_PRE_SOURCE_PRICE`. It labels the result **not original M04 PREALERT** and **not executable PnL**. It reads the required additional pre15 window from the same private archive, capped at existing 20k rows; output is scoped to supplied pair list, avoiding the ad-hoc 13-vs-10 mistake.

No M03/M04/M05 production code, policy, thresholds, retention, message sender, service, private ledger or exchange endpoint is changed. Negative controls, first actual real M04 micro signal and full 24h prospective validation remain outstanding.

## As-of same-minute control cohort: feasibility and limitations (12 Nov 2026)

Owner original VPS used six named event pairs and 10 original matched WS3 owner Bot API ACKs from 11 Nov 23:10–23:37 WIB. The ad-hoc feasibility scan read 1,009 WS3 research rows; retrospective same-minute peers with 0.5–2x rolling 24h turnover, ±90s source epochs and ±15m alert exclusion yielded **8/10 alert events with peers, 18 control selections**. UAI 23:22 had XRP, UAI 23:37 had XRP/SOL/HYPE, NBTNEW had no peers. **18 selections are not 18 independent market events or trades.** A retrospective future ±15m exclusion uses post-alert information and cannot be reused as a prospective no-lookahead comparison.

A new separate **audit-only** script, scripts/m05_ws3_asof_control_price_audit_readonly.py, selects not-yet-alert peers from existing M03 research source rows **recorded before the actual original M05 claim**, from the **same source minute**, with original pair source epoch within 90 seconds, source eligibility status, 0.5–2x rolling-24h IDR turnover and **no prior or current alert claim within 15 minutes**. Future claims are never excluded during selection; if a selected control is subsequently alerted it is flagged descriptively after selection. Controls are peers not-yet-alert, NOT proof of no momentum, no future alert, or random negative cases. No decision changes to original M03/M04/M05.

The script reads only the original M05 private owner SQLite and separately opted-in M03 72h private evidence SQLite using read-only query-only connections. File ownership/mode checks, limited 10 pairs, 120-minute event window, 100 ACKs, 20k research and owner rows, max six peers per alert, deterministic ratio closeness ranking and fail-closed source validation are mandatory. Reuses the existing symmetric 5m/15m last-price proxy function for both alert and selected peers, anchored to actual alert Bot API ACK. Each outcome requires fresh quote snapshots after ACK and after the 5m/15m horizon, within 90 seconds. Missing samples never become losses or zero gains. The report prints observational bps differences only for genuinely measured alert and peer outcomes, with sample coverage counts.

No original source retention, scanner, Telegram cooldown/send path, service, external market request, paid AI or trading execution is touched. Results **do not prove tradable profits, causal superiority, independent returns, verified BUY/SELL trade pressure, executable bid-ask spread, complete market coverage, or a true original M04 micro PREALERT**. Later held-out prospective data, cost/fillability and market microstructure checks remain required before changing trading policy.
