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

**Milestone aktif M03-D:** M03-C sudah 7/7 VPS PASS dan real rolling BTC+ETH exit0. M03-D shared-minute BTC regime guard has been coded on same rolling path; tests and VPS proof pending. All outcomes remain REJECT. Handover in ROADMAP. Semua private API, provider berbayar, dan trading LIVE wajib otorisasi terpisah dan bukti verifikasi sesuai guideline.

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
