# Indodax AI Bot V2

**MISI UTAMA: SCALPING HARIAN INDODAX (24/7 market monitoring, intraday spot trading IDR → BUY → SELL → IDR).** Bot baru untuk spot scalping Indodax, dibangun modular **dari nol** dan **terpisah dari bot lama**. Target akhir: market scanner G1, 3 AI (MARKET → RISK → STRATEGY), Final Decision PASS/REJECT via Telegram MANUAL/AUTO, modal IDR dinamis, protective FLOKI/PEPE, BUY → SELL → IDR reconciliation, monitoring, dan operasi VPS yang aman.

> **Status otoritatif: [ROADMAP.md](docs/ROADMAP.md).** M02 official public market adapter telah CODED dan lulus 16/16 tes lokal (termasuk regresi M01). Observasi penuh REST publik dari VPS, CI, G1 issuer, AI, Telegram, dan order belum terbukti. **Trading LIVE OFF.** Target laba bukan jaminan.

## Mulai di sini

- **[PROJECT GUIDELINE & MASTER CHECKPOINT](docs/PROJECT_GUIDELINE.md)** — **satu sumber kebenaran** untuk seluruh fitur, keputusan owner, arsitektur, acceptance criteria, roadmap, dan progres pengembangan.
- **[ROADMAP & CHECKLIST](docs/ROADMAP.md)** — status pekerjaan yang bisa dicentang, bukti SHA, blocker, milestone aktif dan handover pindah chat. **Satu-satunya tracker kemajuan.**
- **[AGENTS.md](AGENTS.md)** — instruksi singkat untuk coding agent supaya selalu mengikuti milestone aktif dan tidak mengulang pekerjaan.

## IRON RULES

**Jangan hanya audit atau membuat modul yang belum tersambung.** Setelah menetapkan arsitektur, kontrak, struktur repository dan modul utama, implementasikan satu vertical slice lalu lanjut milestone. Setiap tahap harus menunjukkan **(1) kode aktual, (2) wiring, (3) bukti, (4) pending, (5) next action**. Guideline untuk kebijakan, roadmap untuk tracking, no repeat accepted PASS, tanpa bypass keamanan LIVE. Detail lengkap di Bagian **0 dan 0A** guideline.

## Workflow pengembangan

Baca guideline 0/0A/B/E/F → **ROADMAP 1/3/4** → kerjakan **satu** milestone → sambungkan ke mainpath → tes affected scope → update checklist/evidence di **ROADMAP.md** → lanjut.

**Milestone aktif M02:** validasi satu observasi pasar publik langsung pada host dengan koneksi Indodax; tidak menggunakan private key, provider AI ataupun order. Status resmi, bukti dan next action selalu dibaca dari ROADMAP. Semua private API, provider berbayar, dan trading LIVE wajib otorisasi terpisah dan bukti verifikasi sesuai guideline.

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
