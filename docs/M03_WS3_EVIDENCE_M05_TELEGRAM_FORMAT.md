# M03 WS3 Research Evidence + M05 Telegram WATCH Copy

## Owner evidence and limited claim

On 11 Nov 2026 the owner backed up the **original existing** M03 SQLite via a consistent, private backup:

- `m03_ws_summary_watch_minute_ROWS=1408`
- `m03_ws_microstructure_watch_minute_ROWS=204`
- SQLite `quick_check=ok`, backup file 0600 in private 0700 directory.
- No M03/M05 restart and no trading.

The original M03 `market:summary-24h` collector opens a bounded WS3 session; **12 seconds by default** (some deployed units pass 40s). A channel frame may contain only a subset of IDR markets. The original WATCH archive stores one eligible pair/minute and prunes after ~2 hours. Neither a lack of a per-pair row nor an older stale quote establishes absence of exchange trading.

## Change 1: separate, bounded research evidence, opt-in only

New `indodax_ai_bot.m03_ws3_evidence_archive_nonlive` receives the **already-collected original WS3 snapshot after** the original `persist_ws3_summary_watch` completes. It does **not** create a WebSocket/REST request, poll Telegram, or affect the original WATCH candidate selection.

The optional flag is `M03_WS3_EVIDENCE_ARCHIVE_GO=YES`. **Default is off**. A successful original M03 tick stays successful if the optional research archive write fails; that failure is shown only as `research_archive.status=M03_WS3_RESEARCH_ARCHIVE_UNAVAILABLE`. The original M03 2h retention, M05 cooldown, WS3 session length, sample thresholds, and manual final decision are unchanged.

The new independent research SQLite is:
`~/.local/state/m03-ws3-evidence/observations.sqlite3`

- Parent 0700, owned regular DB 0600, rejects untrusted DB/symlink.
- Maximum **72h age**, **250,000 source rows**, **250 pairs per cycle**; row cap can shorten historical coverage even before 72h. No unbounded archive promise.
- `ws3_source_evidence`: per actual observed pair/minute, original exchange timestamp, source price, rolling 24h turnover, original public offset and diagnostic source classification.
- Classifications: eligible snapshot observation, source stale/invalid clock, below Rp150m/24h, invalid price/volume, invalid offset. Excluded/invalid symbols are counted in `ws3_source_cycles`, not leaked as trade recommendations.
- `ws3_source_cycles`: actual observed summary frames, pairs seen, eligible count, stale/low-turnover/invalid counts, dropped-by-cap. If a pair was **never present** in a given actual snapshot, no record exists for it. This does not mean 0 market trades.
- At most one row per pair/minute; duplicate cycles never rewrite the earliest evidence.
- No private tokens, owner IDs, trade orders or AI decisions.

**This is a source preservation mechanism, not a denser feed.** It cannot reconstruct missing BNB/ONL snapshots from the past, nor turn sampled public quotes into actual 1m trades/candles.

### Deployment gate

1. Merge only after GitHub CI + focused offline tests pass.
2. Sync the verified commit on the original VPS and run targeted tests with flags NO/NO/DRY_RUN.
3. Preserve original M03/M05 active services and all DB; do not restore snapshots into live DB.
4. Enable the flag on **the original M03 oneshot service environment only** after checking the installed unit and owner approval. A single unit drop-in (`Environment=M03_WS3_EVIDENCE_ARCHIVE_GO=YES`) takes effect on the next naturally scheduled tick after `systemctl --user daemon-reload`. Do not restart an existing timer/scanner merely to generate evidence.
5. Check `research_archive.status`, original M03 WATCH status, file ownership 0600, and sample/counts without printing secrets. Verify private file remains bounded over subsequent natural cycles.
6. Revert by removing only the new opt-in drop-in and reloading systemd. No original DB migration or trading enablement.

## Change 2: compact WS3 Telegram WATCH

The same existing `send_one_owner_radar_momentum_nonlive` owner-private sender still claims before send, keeps no retries on uncertain outcomes, stores per-pair durable ACK, and has no order buttons. Only the WS3 informational `format_owner_ws3_radar_compact` message text and local helpers change.

Example based on the owner-supplied UCL screenshot, **not an actual new notification**:

    📡 WS3 WATCH | UCL/IDR
    🔺 +11,78% · 14:45:28 → 14:48:40 WIB
    💰 Rp4.543 → Rp5.078
    📊 Nilai 24j Rp174,37 jt · Δ +Rp7,05 jt
    ⚡ Lonjakan besar, waspadai harga terlambat (2 sampel)
    🔬 Trade/order book: belum teramati
    ⚠️ Bukan candle 1 menit. Δ nilai 24j bukan volume transaksi 1 menit atau tekanan beli.
    🔎 Pantauan publik saja. AI/G1, arus trade & kedalaman pasar belum diverifikasi. Bukan rekomendasi beli.

Labels are human-readable Indonesian, avoid raw `EXTENDED_MOVE_REVIEW` identifiers, keep sample timestamps and pair, and retain the explicit risk warning. The original REST radar formatter is unchanged. No speculative BUY/SELL recommendation, no three paid AI agents, no exchange trade or new Telegram bot.

## Independent acceptance

- CI full suite required. Focused original WS3 bridge + new shadow tests + formatting tests required on VPS before new service source is activated.
- New M03 research DB should have `ws3_source_cycles` only after a naturally successful M03 source cycle with explicit opt-in. Empty DB or `UNAVAILABLE` means no proof of live archival.
- New Telegram wording appears only when existing original M05 process is **safely and separately restarted** after owner-private ledger backup and nontrading guards; merely git-fast-forwarding source does not replace the in-memory running M05 process.
- Previous known M05 PID 232877 may change naturally; never treat any hardcoded PID as permanent.
- Neither feature is a reason to enable live execution, lower WATCH thresholds or infer that AI/G1 and full trade-flow have been verified.
