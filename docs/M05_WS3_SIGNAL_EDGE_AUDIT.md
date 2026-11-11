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
