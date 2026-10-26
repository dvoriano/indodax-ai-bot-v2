# Indodax AI Bot V2

**MISI UTAMA: SCALPING HARIAN INDODAX (24/7 market monitoring, intraday spot trading IDR → BUY → SELL → IDR).** Bot baru untuk spot scalping Indodax, dibangun modular **dari nol** dan **terpisah dari bot lama**. Target akhir: market scanner G1, 3 AI (MARKET → RISK → STRATEGY), Final Decision PASS/REJECT via Telegram MANUAL/AUTO, modal IDR dinamis, protective FLOKI/PEPE, BUY → SELL → IDR reconciliation, monitoring, dan operasi VPS yang aman.

> **Status, bukti, blocker dan satu milestone aktif selalu dibaca dari [ROADMAP.md](docs/ROADMAP.md).** M02 pembacaan publik BTC/IDR sudah terverifikasi di VPS; G1 scanner/deep validation, tiga AI, Telegram, dan executor belum siap LIVE. **Trading LIVE OFF.** Target laba bukan jaminan.

## Mulai di sini

- **[PROJECT GUIDELINE & MASTER CHECKPOINT](docs/PROJECT_GUIDELINE.md)** — **satu sumber kebenaran** untuk seluruh fitur, keputusan owner, arsitektur, acceptance criteria, roadmap, dan progres pengembangan.
- **[ROADMAP & CHECKLIST](docs/ROADMAP.md)** — status pekerjaan yang bisa dicentang, bukti SHA, blocker, milestone aktif dan handover pindah chat. **Satu-satunya tracker kemajuan.**
- **[AGENTS.md](AGENTS.md)** — instruksi singkat untuk coding agent supaya selalu mengikuti milestone aktif dan tidak mengulang pekerjaan.

## IRON RULES

**Jangan hanya audit atau membuat modul yang belum tersambung.** Setelah menetapkan arsitektur, kontrak, struktur repository dan modul utama, implementasikan satu vertical slice lalu lanjut milestone. Setiap tahap harus menunjukkan **(1) kode aktual, (2) wiring, (3) bukti, (4) pending, (5) next action**. Guideline untuk kebijakan, roadmap untuk tracking, no repeat accepted PASS, tanpa bypass keamanan LIVE. Detail lengkap di Bagian **0 dan 0A** guideline.

## Workflow pengembangan

Baca guideline 0/0A/B/E/F → **ROADMAP 1/3/4** → kerjakan **satu** milestone → sambungkan ke mainpath → tes affected scope → update checklist/evidence di **ROADMAP.md** → lanjut.

**Milestone aktif M03-G:** M03-F accepted: VPS 29/29 scoped tests PASS and real public quality review exit0, preview empty and Final REJECT. M03-G shared-context 20–40 public deep method CODED and tests pending VPS. Handover in ROADMAP. Semua private API, provider berbayar, dan trading LIVE wajib otorisasi terpisah dan bukti verifikasi sesuai guideline.

**Repo lama:** dvoriano/crypto-bot adalah referensi saja. Jangan menyalin secrets, posisi atau ledger, dan jangan menjalankan dua executor LIVE pada akun yang sama tanpa koordinasi yang sah.

## Menjalankan fondasi M01 (NON-LIVE)

```bash
python -m unittest discover -s tests -v
python -m indodax_ai_bot --db /tmp/indodax-v2.sqlite3 --once
python -m indodax_ai_bot --db /tmp/indodax-v2.sqlite3 --health
```

Pada M01, tanpa G1 source sah keputusan selalu `REJECT`; `execution_allowed=false`. `INDODAX_TRADING_ENABLED=YES`, `INDODAX_ALLOW_LIVE_EXECUTION=YES` atau mode `LIVE` ditolak saat startup. Belum ada koneksi private/exchange maupun paid AI.

## M02: Observasi Indodax PUBLIK (no order)

Hanya pada checkout repository V2 terkini, dengan koneksi internet ke Indodax:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN \
  python3 -m indodax_ai_bot --db /tmp/indodax-v2-m02-public.sqlite3 --verify-public --pair btc_idr
```

Perintah memakai hanya `/api/pairs`, `/api/server_time`, `/api/ticker/<id>`, `/api/trades/<id>`, `/api/depth/<id>` dan `/tradingview/history_v2` dari dokumentasi resmi. Candle 5m dibentuk dari lima candle 1m yang lengkap. Keputusan tetap `REJECT` dengan `execution_allowed=false` karena G1 issuer dan coverage trade belum diotorisasi. Jangan melabel output publik sebagai sinyal BUY.

**M02 strict source probe:** `--verify-public` exit 0 means all required public payloads parsed (`PUBLIC_OBSERVED_G1_UNATTESTED`); exit 3 means public source error. Both cases return `final_decision=REJECT`, `execution_allowed=false` and `live_trading_authorized=false`. Exit 0 does not certify G1 source issuer or allow LIVE trading.

## VPS V2 tanpa menyentuh bot lama

Keputusan owner: **jangan bersihkan/reset VPS lama sekarang**. V2 wajib memakai folder, SSH deploy key read-only, dan runtime terpisah. [Script bootstrap V2](ops/bootstrap_v2_vps.sh) mempunyai dua aksi:

```bash
# Dari checkout V2 (atau gunakan petunjuk setup key awal di chat jika belum bisa clone)
bash ops/bootstrap_v2_vps.sh prepare-key
# Daftarkan PUBLIC key pada repo V2 Settings > Deploy keys (READ ONLY).
bash ops/bootstrap_v2_vps.sh sync-and-probe
```

Tidak menghapus folder, menghentikan service, menyentuh credential Indodax, atau mengaktifkan LIVE. M02_EXIT_CODE=0 hanya membuktikan data publik terbaca, tidak mengesahkan sinyal G1 maupun izin trading.

## M03-A: Uji scanner awal pada VPS (tanpa order)

Setelah `git pull --ff-only origin main` pada checkout V2, jalankan hanya scoped test baru dan satu scan publik:

```bash
INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN \
  python3 -B -m unittest discover -s tests -p 'test_m03a_scanner.py' -v
INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03a.sqlite3 --scan-once
```

Scanner hanya memakai endpoint publik Indodax `/api/pairs`, `/api/server_time`, `/api/ticker_all` dan membuat shortlist awal. Volume 24 jam bukan sinyal scalp yang cukup: **G1 issuer tetap unverified, Final REJECT, order OFF**. M03-B akan mengonfirmasi candle 1/5/15m, BTC, trade-flow dan bukti microstructure sebelum 3 AI.

## M03-B: Verifikasi deep-scoring publik, NON-LIVE

MAIN kini mengecualikan PEPE/FLOKI (dana khusus) dan USDT/USDC (stablecoin settlement). `--deep-once` memakai satu scanner dan maksimal dua deep observations dengan EMA8/21, RSI14, volume, spread, visible flow/book. Ini hasil analisis observasi, **bukan** rekomendasi BUY.

Pada VPS checkout V2 terbaru, jalankan tes scope yang berubah, bukan audit seluruh kode:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m0[23]*.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03b.sqlite3 --deep-once
```

Deep pilot hanya 2 pair, **belum** 20-40 full coverage. Visible trade sample tidak berarti trade history lengkap. G1 issuer tetap unverified, Final REJECT dan eksekusi selalu OFF.


## M03-C: rotasi deep public 2 pair/siklus (belum LIVE/PAPER)

Mode `--rolling-deep-once` memakai **satu scanner dan satu analyzer** yang sudah ada, dengan cursor SQLite dan batas 2 pair per eksekusi; default minimum antarawal scan 12 detik. Reservasi atomik, bisa dilanjutkan setelah restart, dan mencegah dua proses memulai batch serentak. Setiap siklus tetap `REJECT`, data historis antarwaktu **tidak boleh dicampur menjadi ranking live**.

Jalankan pada V2 VPS dengan environment DRY_RUN:

```bash
python3 -B -m unittest discover -s tests -p 'test_m03c_rotation.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03c.sqlite3 --rolling-deep-once
```

Output sukses public-only: `PUBLIC_ROLLING_UNATTESTED`, `reserved_pairs`, `next_index`, `cross_cycle_ranking_allowed=false`, `final_decision=REJECT` dan `execution_allowed=false`. Jika dijalankan kembali dalam kurang dari 12 detik, akan `PUBLIC_ROLLING_BLOCKED`/exit3. Ini tidak memanggil model AI/akun pribadi/order.

## M03-D: Same-minute BTC regime alignment, public-only

Updated existing rolling path, no new scanner or AI. Every pair and BTC context must have the same **closed 1m epoch** and timely observations, otherwise `same_window.status` is a REJECT reason and the list of synchronized diagnostics is empty. An aligned result is still **PUBLIC UNATTESTED**, not qualified for G2, never an execution permission.

Check changed tests on VPS after pulling current SHA:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m03[c-d]*.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03d-samewindow.sqlite3 --rolling-deep-once
```

Do not mix candidate scores from different one-minute candle closes. Historical coverage across 20-40 assets is future work, not evidence of one synchronous decision. Final remains REJECT.

## M03-E: provisional extreme-momentum review gate (non-live)

VPS M03-D 15/15 tests PASS on 5f14be87. Real FARTCOIN/IDR last-minute zero volume was rejected, while BR/IDR yielded diagnostic score 80 with RSI100, 82.89x volume, sampled visible sells much larger than sampled buys. M03-E adds review flags to **existing** deep_analysis, not a new scanner or BUY condition.

Provisional cutoffs: last closed-minute volume >=10x preceding 10-min average combined with RSI >=90 and/or visible sell notional >=3x sampled buy. This is conservative anomaly screening (not evidence of fraud, not an empirically optimized probability). An anomalous candidate gets score `null`, `REJECT_MOMENTUM_ANOMALY`, `anomaly_flags`, and cannot pass same-window diagnostic ranking. Thresholds must be historically replayed before reliance for trading.

One scoped VPS check after updating repository:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m03[b-e]*.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03e.sqlite3 --rolling-deep-once
```

No G1 provenance, trade coverage, AI, Telegram or live exchange permission is enabled by this milestone.

## M03-F: observable quality cautions and a non-trading preview

VPS M03-E reached **29/29 PASS**, real aligned public BTC+ETH source and `M03E_EXIT_CODE=0` at 4cf220a7. BTC regime then was DOWNTREND and the pair diagnostics were both score30, with negative book imbalance. These are timestamped samples, not real-time guidance.

M03-F adds provisional caution flags to the **same** analyzer: BTC downtrend, RSI <=30 / >=75, 1m relative volume below 1, book imbalance below -0.5, or sampled visible sells above buys. A synchronized public-only preview may include up to 2 candidates with zero caution flags. This preview is NOT a qualified G1 receipt or a trading signal, all guard booleans remain false. Cutoffs have not been validated by historical replay.

One VPS verification of affected tests and public market:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m03[d-f]*.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03f.sqlite3 --rolling-deep-once
```

Actual trade history coverage and G1 issuer are NOT present; Final Decision remains REJECT, private keys and orders OFF.

## M03-G: Optimized public 20–40 source observations, no-trade

Official Indodax Public REST API documents **180 requests/minute**: https://github.com/btcid/indodax-official-api-docs/blob/master/Public-RestAPI.md. Current per-pair adapter historically fetched 7 GET per pair, too expensive for 30+. **New shared-context path** reuses three scanner responses (pairs/server_time/ticker_all), leaving four per-pair GET: OHLC 1m/15m, public trades, depth. For 20 pairs estimate 83 GET (or 87 if BTC outside batch); for 40 163 (or 167 extra BTC). Count is only a single-cycle estimate and does NOT include other processes on the shared VPS public IP. Keep conservative 12 GET reserve.

`--bulk-deep-once` defaults to **20** and stops collecting after 30 seconds from scanner observation. Results are only comparable if every selected pair is valid and aligned to the same closed 1m/5m/15m context with BTC. Otherwise `same_window.status` rejects and any synchronized ranking/preview remains empty. `trade_history_complete=false`, `g1_verified=false`, Final REJECT, order OFF regardless of success.

Only after a clean Git pull on VPS, test changes to M02 adapter, M03-A scanner, and new M03-G:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m02_public_market.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m03a_scanner.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m03g_bulk.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03g-bulk.sqlite3 --bulk-deep-once
```

These are exactly the three affected scopes: M02 public adapter, M03-A scanner and M03-G bulk. Previously accepted M03-B/C/D/E/F are not rerun unless they break due to an actual dependency change. 20 live public pair requests can still fail or time out; this is not a scanner malfunction by itself. Do NOT enable orders.
