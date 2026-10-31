# AGENTS.md — Indodax AI Bot V2

Berlaku untuk semua pekerjaan coding AI/agent dalam repository ini.

**Satu referensi kanonis:** [docs/PROJECT_GUIDELINE.md](docs/PROJECT_GUIDELINE.md). Baca Bagian 0/0A/B/E/F guideline sebagai aturan, lalu [docs/ROADMAP.md](docs/ROADMAP.md) untuk milestone aktif, checklist, evidence dan handover. **Roadmap ini satu-satunya pelacak**; jangan membuat roadmap/status/handover kedua.

## LOCK RUANG KERJA (KEPUTUSAN OWNER 31 OKTOBER 2026)

**ALLOWLIST TUNGGAL:** seluruh pekerjaan pengembangan, pemeriksaan, perubahan kode, PR, audit acceptance, dan handover hanya pada **GitHub `dvoriano/indodax-ai-bot-v2`** (beserta branch/PR di repo yang SAMA). Pekerjaan diarahkan ke **SATU V2 `DailyScalpingOrchestrator` dan runtime V2 yang sama**, dengan `docs/ROADMAP.md` sebagai satu-satunya tracker.

**DENY BY DEFAULT:**
- Jangan membuka, membaca, mencari, mengubah, menyalin, mengimpor atau membangun ulang sumber dari repo **`dvoriano/crypto-bot` (V1)** ataupun repo lain, meskipun hanya untuk referensi; **jangan gunakan V1 sebagai basis fitur baru**. Kesepakatan historis migrasi sudah tercermin pada kode V2.
- Jangan mengutak-atik proses/checkout/unit/database/secret **V1, Hermes, peer lain, atau proyek lain**; jangan menghentikan/menghapus layanan itu dari pekerjaan di V2.
- Jangan membuat repo, master ledger, roadmap, scanner, AI orchestrator, Telegram poller, executor, atau ledger kedua. Modul baru **hanya jika** checklist V2 yang sudah ada memerlukan fungsi yang belum tersedia dan integrasinya ke caller asli dibuktikan.
- Untuk tugas coding sekarang gunakan **GitHub V2 saja**. Jangan melakukan SSH/VPS deploy, restart, perubahan systemd, akun privat, paid AI, atau trading tanpa instruksi/GO owner **yang terpisah dan eksplisit**. Aktivitas public market yang memang sudah menjadi bagian bot V2 tidak otomatis dilarang oleh batas ruang kerja pengembangan ini, tetapi G1/permission tetap fail-closed.
- Jangan menyebut aturan repo sebagai pengunci izin GitHub atau firewall secara teknis: ini **pembatasan tindakan agent**; izin konektor/platform dan service eksternal tidak otomatis berubah.

**PENEGAKAN SETIAP GILIRAN:** sebelum memanggil tool atau membuat perubahan, pastikan **target repository persis `dvoriano/indodax-ai-bot-v2`**, target checklist asli V2, dan file V2 yang akan diubah. Bila permintaan mensyaratkan akses di luar allowlist, **STOP** pada batas itu dan minta izin owner untuk pengecualian spesifik. Jangan menganggap kata "lanjut" sebagai izin membuka V1. Jangan membuat migrasi ulang, jangan mengubah daftar `[x]/[ ]` tanpa acceptance baru.

## SOP PENERIMAAN LIMA TAHAP (KEPUTUSAN OWNER 31 OKTOBER 2026)

**Scope tetap 98 checklist asli** di [docs/ROADMAP.md](docs/ROADMAP.md). **49 butir historis [x] dibiarkan tetap selesai untuk saat ini; jangan audit ulang atau mengubahnya sebelum pemeriksaan kesiapan gabungan menjelang LIVE.** Kerjakan 49 butir yang masih [ ] melalui [docs/OPEN_CHECKLIST_STAGES.md](docs/OPEN_CHECKLIST_STAGES.md), yang merupakan **tampilan tahapan dari checklist asli, bukan roadmap kedua atau scope baru**. Rekonsiliasikan statusnya dengan ROADMAP saat setiap acceptance tuntas.

Untuk setiap checklist terbuka, gunakan lima gerbang:
1. **Kode**: implementasikan semua fungsi checklist sampai utuh; jangan berhenti pada kerangka atau file terisolasi. Memerlukan identitas kode/commit.
2. **Audit**: targeted positive/negative integration, kegagalan, replay/restart, invariant security bila relevan. Jangan ulang tes PASS yang sudah diterima tanpa perubahan substansial. Cantumkan SHA, suite, dan hasil.
3. **Terhubung bot**: buktikan caller resmi (CLI/systemd/Telegram) melalui SATU DailyScalpingOrchestrator ke modul produksi dan output/ledger. Mock/fixture dan import terisolasi tidak cukup.
4. **Deploy**: verifikasi exact pinned source, installed unit/config dan operasi VPS V2 yang sama. Staging/worktree/DRAFT PR bukan deploy. Untuk checklist yang secara inheren berupa dokumen/izin non-runtime, beri catatan N/A beralasan, bukan centang deploy palsu.
5. **Selesai**: hanya ketika acceptance asli item terpenuhi dan bukti tercatat. Ubah checkbox asli [ ] menjadi [x] di ROADMAP dan tahap Selesai di tracker pada perubahan yang sama.

**Urutan kerja**: tutaskan implementasi dan integrasi yang diizinkan untuk satu checklist, lakukan satu penerimaan gabungan yang tepat, baru lanjut checklist lain. Tidak meminta owner mengulang tes mikro berturut-turut untuk kode parsial. Jika izin private-account, paid AI, network atau LIVE diperlukan, STOP di gerbang itu; persiapan offline diperbolehkan tetapi tidak dianggap selesai. Tidak ada GO tersirat. Sebelum LIVE, periksa ulang kesiapan gabungan seluruh 98 item, termasuk 49 historis, tanpa mengubah status historis sekarang. 

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
- Repo lama `dvoriano/crypto-bot` **DI LUAR ALLOWLIST dan tidak boleh diakses**, termasuk sebagai referensi, kecuali owner memberikan pengecualian eksplisit untuk suatu tugas; service dan secrets lama tidak disentuh.
- Uji hanya fungsi berubah dan acceptance yang belum terbukti; jangan ulang tes accepted PASS tanpa perubahan relevan.
- Setelah menyelesaikan milestone, **update docs/ROADMAP.md saja untuk checklist, SHA, bukti, kekurangan dan satu next action**. Ubah guideline hanya jika kebijakan/spesifikasi disetujui berubah.
- Dilarang menyatakan fitur LIVE jika hanya mock, offline, public VIEW atau read-only. Jaga keamanan credentials, ownership order, dan fail-closed.
- Jika bot lama dan baru berada di akun Indodax yang sama, hanya satu boleh bertransaksi LIVE tanpa koordinasi saldo/posisi lintas-bot terverifikasi.

## Protokol ANTI-LOOP: wajib sebelum setiap tindakan

Protokol ini mengikat setiap respons implementasi dan handover, termasuk ketika owner berkata "lanjut"/"kerjakan" atau berpindah chat. **Jangan jadikan audit, pemeriksaan ulang, tes, dokumen tambahan, monitoring tambahan, atau staging sebagai pekerjaan utama apabila tidak langsung menutup checklist yang sudah ada.**

1. **Pilih dari item `[ ]` yang SUDAH ADA di Bagian 3 ROADMAP.** Terapkan urutan dependensi owner. Sebelum mengedit kode, mengusulkan perintah VPS, membuat PR, atau memeriksa ulang, tulis singkat: `TARGET: [teks item checklist asli]`; `SUDAH ADA: [bukti PASS/SHA terkait]`; `GAP: [satu syarat acceptance spesifik yang belum terpenuhi]`; `AKSI: [perubahan/deliverable konkret untuk menutup GAP]`. Jika GAP tidak bisa disebut secara spesifik, JANGAN mengulang audit.
2. **Reuse evidence first, zero-repeat by default.** Baca register bukti Bagian 4, source SHA yang diterima, dan hasil owner sebelumnya. Dilarang meminta ulang command, memeriksa service yang sama, atau menjalankan tes PASS yang sama hanya untuk "memastikan". Pengecualian hanya jika ada perubahan code/config yang memengaruhi acceptance tersebut, bukti sebelumnya kedaluwarsa pada syarat yang eksplisit, atau insiden baru; sebutkan perubahan, tes minimal, dan mengapa bukti lama tidak cukup.
3. **Kirim implementasi, bukan lingkaran diagnostik.** Untuk gap code/wiring: edit modul asli yang sudah ada, lakukan satu targeted proof pada caller original, lalu catat hasil. Jangan membuat scanner/AI/executor paralel atau lapisan monitoring baru sebagai substitusi. Perintah SSH owner hanya jika ada bukti nyata yang tidak dapat diperoleh dari repo dan benar-benar diperlukan untuk checklist target; singkat, aman ditempel ke shell interaktif, tidak memakai `exit`/`set -e`/`trap` yang bisa mengakhiri sesi. Jangan menyentuh service/secrets di luar scope.
4. **BLOCKED bukan tugas audit berulang.** Jika item memerlukan izin owner, sumber eksternal, bukti IP-wide, atau syarat yang belum tersedia, tulis `BLOCKED: [dependensi konkret]` sekali. Jangan mengulang item itu sampai dependensi berubah. Kerjakan item `[ ]` lain yang independen sesuai urutan dependensi; jangan menyatakan item terblokir selesai, menghapusnya, atau melonggarkan G1/risk/safety.
5. **Hitung hanya checklist, bukan jumlah PASS.** Unit test, staging, canary, dan sub-bukti masuk register bukti tanpa membuat `[x]` baru. Hanya centang item yang SUDAH ADA setelah semua acceptance-nya terpenuhi, sehingga `selesai +1` dan `terbuka -1`. Jangan mengubah definisi atau membuat item baru tanpa persetujuan eksplisit owner. Jika belum selesai, angka tidak berubah.
6. **Laporkan hanya delta kerja:** target item, kode/wiring yang sungguh berubah, bukti baru (atau "belum"), blocking condition, dan satu next action yang mengarah pada penyelesaian item `[ ]`. Jangan memaparkan ulang cerita seluruh M03/M04 setiap giliran. Jangan mengklaim implementasi kalau baru memeriksa kode.

Jika jawaban yang akan diberikan justru mengulang tes PASS, membuat side-feature, atau meminta command inspeksi yang sudah pernah berhasil tanpa kondisi baru, **batalkan langkah itu dan kembali ke item target**. Keamanan trading, izin owner, dan gate G1 tetap tidak boleh dilompati.

## Cara melaporkan pekerjaan
**Milestone → file/fungsi yang berubah → jalur runtime yang tersambung → tes/hasil → status sebenarnya → next action.** Maksimal satu langkah berikutnya, tanpa mengulang laporan lama.

**Urutan handover:** guideline 0/0A/B/E/F → ROADMAP 1/3/4 → kode affected-scope → implementasi → update ROADMAP. Status bukan disalin ke guideline.
