# Indodax AI Bot V2

**MISI UTAMA: SCALPING HARIAN INDODAX (24/7 market monitoring, intraday spot trading IDR → BUY → SELL → IDR).** Bot baru untuk spot scalping Indodax, dibangun modular **dari nol** dan **terpisah dari bot lama**. Target akhir: market scanner G1, 3 AI (MARKET → RISK → STRATEGY), Final Decision PASS/REJECT via Telegram MANUAL/AUTO, modal IDR dinamis, protective FLOKI/PEPE, BUY → SELL → IDR reconciliation, monitoring, dan operasi VPS yang aman.

> **Status:** M01 fondasi Python **CODED + TESTED_OFFLINE (7/7 lokal PASS)**; Indodax public G1/AI/Telegram/order belum terhubung. GitHub CI run #1 gagal sebelum menjalankan tes, belum verified CI. **Trading LIVE OFF.** Target laba bukan jaminan.

## Mulai di sini

- **[PROJECT GUIDELINE & MASTER CHECKPOINT](docs/PROJECT_GUIDELINE.md)** — **satu sumber kebenaran** untuk seluruh fitur, keputusan owner, arsitektur, acceptance criteria, roadmap, dan progres pengembangan.
- **[ROADMAP & CHECKLIST](docs/ROADMAP.md)** — status pekerjaan yang bisa dicentang, bukti SHA, blocker, milestone aktif dan handover pindah chat. **Satu-satunya tracker kemajuan.**
- **[AGENTS.md](AGENTS.md)** — instruksi singkat untuk coding agent supaya selalu mengikuti milestone aktif dan tidak mengulang pekerjaan.

## IRON RULES

**Jangan hanya audit atau membuat modul yang belum tersambung.** Setelah menetapkan arsitektur, kontrak, struktur repository dan modul utama, implementasikan satu vertical slice lalu lanjut milestone. Setiap tahap harus menunjukkan **(1) kode aktual, (2) wiring, (3) bukti, (4) pending, (5) next action**. Guideline untuk kebijakan, roadmap untuk tracking, no repeat accepted PASS, tanpa bypass keamanan LIVE. Detail lengkap di Bagian **0 dan 0A** guideline.

## Workflow pengembangan

Baca guideline 0/0A/B/E/F → **ROADMAP 1/3/4** → kerjakan **satu** milestone → sambungkan ke mainpath → tes affected scope → update checklist/evidence di **ROADMAP.md** → lanjut.

**M01 foundation sudah di-code dan tercatat tes lokal 7/7 PASS (CI belum terverifikasi)**. Milestone aktif berikutnya **M02**, sambungan market data publik Indodax ke orchestrator. Detail terkini dan status resmi ada di ROADMAP, bukan baris README ini. Semua private API, provider berbayar, dan trading LIVE wajib otorisasi terpisah dan bukti verifikasi sesuai guideline.

**Repo lama:** dvoriano/crypto-bot adalah referensi saja. Jangan menyalin secrets, posisi atau ledger, dan jangan menjalankan dua executor LIVE pada akun yang sama tanpa koordinasi yang sah.

## Menjalankan fondasi M01 (NON-LIVE)

```bash
python -m unittest discover -s tests -v
python -m indodax_ai_bot --db /tmp/indodax-v2.sqlite3 --once
python -m indodax_ai_bot --db /tmp/indodax-v2.sqlite3 --health
```

Pada M01, tanpa G1 source sah keputusan selalu `REJECT`; `execution_allowed=false`. `INDODAX_TRADING_ENABLED=YES`, `INDODAX_ALLOW_LIVE_EXECUTION=YES` atau mode `LIVE` ditolak saat startup. Belum ada koneksi private/exchange maupun paid AI.
