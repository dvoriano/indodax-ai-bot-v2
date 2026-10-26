# AGENTS.md — Indodax AI Bot V2

Berlaku untuk semua pekerjaan coding AI/agent dalam repository ini.

**Satu referensi kanonis:** [docs/PROJECT_GUIDELINE.md](docs/PROJECT_GUIDELINE.md). Baca Bagian A (checkpoint), B (keputusan tetap), E (roadmap), F (aturan kerja) pada awal tiap sesi, lalu C/D sesuai milestone. Jangan membuat master ledger, roadmap, status, atau handover kedua.

## Aturan eksekusi
- Ikuti **satu milestone aktif** pada guideline; jangan berganti sasaran atau mengulang desain dari nol.
- Coding harus membuktikan wiring ke satu entrypoint/orchestrator utama. Modul yang tidak dipanggil runtime belum selesai.
- Gunakan provider interface, typed schema, source receipts, deterministic risk gates, persistence, idempotency, dan fail-closed.
- Tepat tiga AI role: MARKET → RISK → STRATEGY (RISK veto). Final owner-facing **PASS/REJECT**; Telegram MANUAL/AUTO tidak menimpa risk.
- Trading MAIN: IDR → BUY → SELL → IDR; 1–2 pair, ROI BUY diproyeksikan ≥2% bersih **dan** laba ≥Rp10.000 bersih; target SELL 2%/8%/11% netto. Target harian Rp100.000 netto terealisasi bukan jaminan.
- FLOKI 25% trading / 75% reserve; PEPE 30% trading / 70% reserve; dana rebuy terproteksi.
- No private GET/trade atau paid provider usage tanpa scope izin yang jelas. Order LIVE default OFF dan tidak pernah dinyalakan hanya karena tes PASS.
- Repo lama dvoriano/crypto-bot **read-only reference**, bukan kode yang ditulis otomatis. Jangan menyentuh service atau secrets bot lama.
- Uji hanya fungsi berubah dan acceptance yang belum terbukti; jangan ulang tes accepted PASS tanpa perubahan relevan.
- Setelah menyelesaikan milestone, **update A dan E dalam guideline yang sama**: SHA, apa yang benar-benar diuji, keterbatasan, langkah berikutnya.
- Dilarang menyatakan fitur LIVE jika hanya mock, offline, public VIEW atau read-only. Jaga keamanan credentials, ownership order, dan fail-closed.
- Jika bot lama dan baru berada di akun Indodax yang sama, hanya satu boleh bertransaksi LIVE tanpa koordinasi saldo/posisi lintas-bot terverifikasi.

## Cara melaporkan pekerjaan
**Milestone → file/fungsi yang berubah → jalur runtime yang tersambung → tes/hasil → status sebenarnya → next action.** Maksimal satu langkah berikutnya, tanpa mengulang laporan lama.
