# AGENTS.md — Indodax AI Bot V2

Berlaku untuk semua pekerjaan coding AI/agent dalam repository ini.

**Satu referensi kanonis:** [docs/PROJECT_GUIDELINE.md](docs/PROJECT_GUIDELINE.md). Baca Bagian 0/0A/B/E/F guideline sebagai aturan, lalu [docs/ROADMAP.md](docs/ROADMAP.md) untuk milestone aktif, checklist, evidence dan handover. **Roadmap ini satu-satunya pelacak**; jangan membuat roadmap/status/handover kedua.

## IRON RULES (instruksi tertinggi pengembangan repo)
- **DAILY SCALPING HARIAN 24/7 adalah kemampuan utama bot.** Satu jalur nyata: closed 1m + 5m/15m/BTC → G1 scan/rank 20–40 top 1–2 → MARKET/RISK/STRATEGY → Final MANUAL/AUTO PASS/REJECT → last-moment preflight → BUY → managed SELL → IDR realized PnL WIB. Hindari overnight HOLD, no forced trade.
- **MULAI IMPLEMENTASI setelah rencana disusun.** Arsitektur, kontrak, struktur repo dan modul harus segera ditindaklanjuti dengan vertical slice runnable, bukan audit/dokumen berulang.
- **LAPORAN WAJIB TIAP TAHAP 5 BAGIAN:** (1) kode yang dibuat, (2) wiring mainpath, (3) proof dan SHA, (4) belum berfungsi, (5) satu next action. Jangan mengaku DONE jika modul belum jalan.
- **SECURITY VETO ABSOLUT:** tanpa GO LIVE terpisah, private API scope, source provenance, saldo verified, Risk ALLOW, ownership, atomic execution, dan recovery jangan kirim BUY/SELL; owner PASS tidak membypass.
- Baca pula Bagian **0 dan 0A** pada guideline kanonis sebelum kerja; perubahan arah hanya dengan keputusan owner.

## Aturan eksekusi
- Ikuti **satu milestone aktif di ROADMAP** yang harus selaras dengan definisi milestone guideline; jangan berganti sasaran atau mengulang desain dari nol.
- Coding harus membuktikan wiring ke satu entrypoint/orchestrator utama. Modul yang tidak dipanggil runtime belum selesai.
- Gunakan provider interface, typed schema, source receipts, deterministic risk gates, persistence, idempotency, dan fail-closed.
- Tepat tiga AI role: MARKET → RISK → STRATEGY (RISK veto). Final owner-facing **PASS/REJECT**; Telegram MANUAL/AUTO tidak menimpa risk.
- Trading MAIN: IDR → BUY → SELL → IDR; 1–2 pair, ROI BUY diproyeksikan ≥2% bersih **dan** laba ≥Rp10.000 bersih; target SELL 2%/8%/11% netto. Target harian Rp100.000 netto terealisasi bukan jaminan.
- FLOKI 25% trading / 75% reserve; PEPE 30% trading / 70% reserve; dana rebuy terproteksi.
- No private GET/trade atau paid provider usage tanpa scope izin yang jelas. Order LIVE default OFF dan tidak pernah dinyalakan hanya karena tes PASS.
- Repo lama dvoriano/crypto-bot **read-only reference**, bukan kode yang ditulis otomatis. Jangan menyentuh service atau secrets bot lama.
- Uji hanya fungsi berubah dan acceptance yang belum terbukti; jangan ulang tes accepted PASS tanpa perubahan relevan.
- Setelah menyelesaikan milestone, **update docs/ROADMAP.md saja untuk checklist, SHA, bukti, kekurangan dan satu next action**. Ubah guideline hanya jika kebijakan/spesifikasi disetujui berubah.
- Dilarang menyatakan fitur LIVE jika hanya mock, offline, public VIEW atau read-only. Jaga keamanan credentials, ownership order, dan fail-closed.
- Jika bot lama dan baru berada di akun Indodax yang sama, hanya satu boleh bertransaksi LIVE tanpa koordinasi saldo/posisi lintas-bot terverifikasi.

## Cara melaporkan pekerjaan
**Milestone → file/fungsi yang berubah → jalur runtime yang tersambung → tes/hasil → status sebenarnya → next action.** Maksimal satu langkah berikutnya, tanpa mengulang laporan lama.

**Urutan handover:** guideline 0/0A/B/E/F → ROADMAP 1/3/4 → kode affected-scope → implementasi → update ROADMAP. Status bukan disalin ke guideline.
