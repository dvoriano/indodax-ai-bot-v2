# PROJECT GUIDELINE & MASTER CHECKPOINT
## INDODAX AI BOT V2

**Repository:** dvoriano/indodax-ai-bot-v2 (PRIVATE)  
**Baseline:** 26 Oktober 2026  
**Canonical source:** dokumen ini. **GitHub = source of truth; VPS = environment pengujian/runtime; Indodax = sumber data saldo, order, dan fill nyata.**

> **BACA DULU SETIAP CHAT / SESI CODING:** Bagian A (checkpoint), B (keputusan tetap), C (arsitektur), D (kontrak dan keselamatan), E (roadmap), F (cara kerja). Tidak perlu meninjau ulang arsip panjang ataupun mengulang tes PASS jika fungsi tidak berubah.

## A. CHECKPOINT PROYEK (SATU-SATUNYA STATUS KANONIS)

| Item | Status awal |
| --- | --- |
| Sasaran | Membangun bot spot scalping Indodax V2 dari repository baru dengan satu jalur online G1 → 3 AI → keputusan → execution → position/SELL → IDR dan kontrol Telegram. |
| Implementasi | **BELUM DIMULAI.** Dokumen ini menyatakan kebutuhan/acceptance, **bukan** bukti bahwa bot sudah aktif. |
| Milestone aktif | **M01: fondasi runnable**, struktur paket, konfigurasi aman, kontrak data, persistence dan test dasar; jangan mengimplementasikan banyak modul tanpa wiring. |
| Milestone selesai | M00: guideline/roadmap ditetapkan (dokumen); belum ada software, VPS, AI paid, private API, atau order yang diuji. |
| Live trading | **OFF / TIDAK DIIJINKAN** sampai seluruh kriteria release terverifikasi dan ada persetujuan owner yang terpisah dan eksplisit untuk order uang nyata. |
| Bukti penerimaan | Catat pada tabel roadmap: SHA, tes yang lulus, lintasan runtime yang terbukti, keterbatasan, dan langkah berikutnya. |
| Satu langkah berikutnya | Buat skeleton runnable dengan satu entrypoint, interfaces dan failing-safe settings; rangkai lalu uji satu alur vertikal non-live. |

**Definisi status yang wajib dipakai:** NOT_STARTED, CODED, TESTED_OFFLINE, VERIFIED_PUBLIC_ONLINE, VERIFIED_PRIVATE_READONLY, VERIFIED_PAPER, VERIFIED_LIVE, BLOCKED. Gunakan status paling tinggi yang benar-benar didukung bukti, jangan lompat dari mock ke LIVE. Tanpa bukti, tetap CODED atau BLOCKED.

Setiap checkpoint setelah perubahan cukup memperbarui bagian A dan tabel E. **Jangan membuat file ledger/handover/status/checklist paralel.**

## B. KEPUTUSAN PRODUK YANG TETAP

| Area | Kebutuhan final |
| --- | --- |
| Platform | Indodax spot, modal MAIN dalam IDR, Python di Ubuntu VPS, kode di GitHub, kontrol Telegram privat. |
| Strategi | Scalping; alur utama IDR → BUY → SELL → IDR, sebisa mungkin tidak HOLD tanpa alasan dan proteksi exit. |
| Profit | Target **Rp100.000 netto terealisasi/hari** sebagai tujuan, **bukan jaminan** dan **bukan alasan memaksa entry**. |
| Gerbang BUY | Estimasi profit **minimal Rp10.000 BERSIH DAN ROI ≥2% BERSIH** setelah fee round-trip, spread, slippage dan asumsi fill yang konservatif. Boleh NO_TRADE. Tidak ada batas internal wajib Rp100.000 per transaksi. |
| Target SELL MAIN | **2% / 8% / 11% NETTO**, bukan 2/5/10. Trailing, stop-loss, time-stop dan protective SELL wajib. Protective SELL boleh rugi untuk mengurangi risiko, tidak tunduk pada profit floor BUY. |
| Banyak posisi | Maksimal **1–2 pair MAIN** dipilih berdasarkan kualitas, tidak wajib mengisi dua slot. |
| AI | Tepat **3 role berurutan**: MARKET → RISK (veto) → STRATEGY. Bukan empat AI; Final Decision bersifat kebijakan deterministik / owner approval. |
| Model | Provider bisa dikonfigurasi melalui adapter (mis. Sumopod). Calon konfigurasi awal ketiga role: glm-5.3-flash **hanya jika tersedia dan kompatibel**; model dapat diganti tanpa merombak sistem, perubahan dicatat. Jangan memasang kuota request/token harian arbitrer tanpa instruksi owner; ukur penggunaan/biaya. |
| Final Decision | Hanya **PASS / REJECT** sebagai hasil proposal. NO_TRADE, stale, veto, kill-switch dan fault di jalur internal menghasilkan REJECT, bukan pilihan owner ketiga bernama STOP. |
| Telegram | **MANUAL** default; owner bisa memilih **AUTO**. MANUAL menampilkan PASS/REJECT dengan expiry **maksimal 90 detik**, single-use dan terikat proposal. AUTO tetap tunduk pada semua gate. |
| Modal | MAIN memakai free IDR yang fresh, dikurangi reservasi FLOKI/PEPE serta komitmen kas belum dihitung oleh locked. Top-up, hasil penjualan, dan profit terealisasi masuk menurut aturan ownership, tanpa double-count. |
| FLOKI | 25% dari holding FLOKI untuk trading khusus FLOKI, 75% reserve. IDR hasil SELL FLOKI hanya untuk rebuy FLOKI. |
| PEPE | 30% dari holding PEPE untuk trading khusus PEPE, 70% reserve. IDR hasil SELL PEPE hanya untuk rebuy PEPE. |
| Pengamanan | Source freshness, G1 provenance, Risk veto, auth, batas modal, fee/precision, bot-owned order/position, idempotency, fill/restart reconciliation, kill-switch selalu fail-closed. |
| Pemisahan | Bot V2 **independen** dari repo lama dvoriano/crypto-bot; repo lama referensi fitur, bukan sumber kode otomatis. Dilarang memodifikasi bot lama sebagai efek samping. |

**PERINGATAN MULTI-BOT:** Jika bot lama dan V2 terhubung pada akun Indodax yang sama, jangan jalankan keduanya LIVE bersamaan tanpa mekanisme lintas-bot untuk reservasi kas, ownership posisi dan rekonsiliasi. Default aman: hanya satu executor LIVE pada akun tersebut. API key dan runtime dipisahkan; jangan menganggap key terpisah mencegah double-spend pada akun yang sama.

## C. TARGET ARSITEKTUR TUNGGAL

Indodax public ticker / trades / order book / OHLCV
→ G1 scanner dan market data validation
→ G1 technical + microstructure analysis
→ G1 ranking dan max-2 candidate selection
→ context snapshot terikat pair dan closed-candle
→ AI MARKET → AI RISK → AI STRATEGY
→ trading decision policy
→ final proposal MANUAL owner Telegram atau AUTO (PASS/REJECT)
→ final deterministic risk/capital/precision/ownership checks
→ execution preflight
→ Indodax execution engine
→ persistent position manager / SELL lifecycle
→ exchange order-fill-IDR wallet reconciliation
→ realized PnL, Telegram dan audit trail.

**Satu implementation per tanggung jawab.** Tidak membuat scanner V3/V4/V5 baru ketika sudah ada scanner resmi di V2; tidak membangun bridge alternatif atau dua decision engine yang berkompetisi. Routing jelas dalam satu application orchestrator. Semua engine dikendalikan oleh state yang persisten dan dapat dilanjutkan setelah restart.

### C1. G1 Scanner, ranking dan bukti pasar
- Discovery seluruh pair aktif yang berdenominasi IDR; filter instrumen, minimum notional, spread, volume, likuiditas dan kondisi listing sesuai data yang tersedia.
- Screening ringan seluruh pair yang layak → analisis mendalam **sekitar 20–40** kandidat → ranking deterministik → **maksimal 2** kandidat layak untuk tiga AI. Jika tidak layak, hasil NO_TRADE / Final REJECT.
- Harga, perubahan harga, order-book depth, bid/ask imbalance, spread, volume, volatility, trade-flow BUY/SELL, surge, acceleration, pullback, breakout, continuation, reversal, rejection, false breakout, BTC regime.
- Candle **1m / 5m / 15m**; EMA, RSI, Bollinger Bands, dan indikator lain hanya ketika bukti mendukung manfaatnya. Simpan timeframe, waktu tutup, sumber dan data coverage.
- Closed-candle wajib; never treat in-progress candle as closed. Nol transaksi bukan candle palsu: gunakan interval kosong dengan penandaan yang benar bila sesuai aturan sumber. Jika historis terpotong/page-limited, laporkan coverage yang terbukti, jangan klaim 1 jam lengkap.
- Validasi market/candle/trade-join, freshness, continuity, source ownership/provenance, clock skew dan source expiry; kekurangan bukti = REJECT.
- Ranking/tie-break deterministik. Threshold skor mesti eksplisit, dapat diuji lewat historical ablation, bukan perubahan spontan untuk memaksa BUY.

### C2. Tepat tiga AI
**MARKET**: memahami arah BTC/altcoin, 1/5/15m, volume, spread, likuiditas, momentum, buyer/seller, breakout/pullback/reversal, anomali serta listing/delisting berdasarkan bukti tersedia. Output dengan alasan dan evidence refs; dilarang membuat candle/volume/berita fiktif.

**RISK (VETO)**: evaluasi sumber, volatilitas, likuiditas, potensi manipulasi, slippage, downside, concentration, stop-loss, exposure, balance dan listing risk. Kesimpulan terstruktur ALLOW / NO_TRADE / EXIT_REVIEW. NO_TRADE memveto BUY dan tidak bisa dibatalkan STRATEGY/owner.

**STRATEGY**: menganalisis peluang entry, entry-zone/price, sizing, jenis order, anticipated fee/slippage, konservatif projected net PnL/ROI, trailing/exit dan kondisi yang membatalkan entry. Wajib mematuhi MARKET evidence dan RISK veto.

- Ketiganya menerima **snapshot market yang sama** untuk pair yang sama, bukan query tak sinkron yang berpura-pura terikat.
- JSON/typed schema divalidasi; timeout, missing role, malformed response, stale source, provider error atau uncertainty wajib fail-closed.
- Provider/model dapat diganti per role melalui konfigurasi tanpa mengubah kontrak; ukur latency, response validity dan biaya.
- Jangan menganggap hasil AI adalah bukti otorisasi order. Semua aturan keras ditegakkan kode.
- Final Decision bukan AI keempat.

### C3. Final Decision dan Telegram
- **MANUAL** default: hanya proposal yang seluruh prasyarat market, tiga AI, Risk, modal, biaya, precision dan eligibility lolos yang mendapat tombol owner PASS/REJECT. Kirim pair, candle-close, proposal ID, entry, sizing, estimated profit netto, ROI, risiko, expiry dan sumber.
- PASS harus single-use, terikat owner identity dan proposal payload/market hash, expired maksimum 90s atau lebih cepat bila invalid; gunakan transactional compare-and-set untuk concurrency. REJECT membatalkan. Tak ada replay/reuse.
- PASS bukan izin melewati risk, freshness, balance, ownership atau preflight; setelah klik wajib cek ulang kondisi exchange. Jika berubah, REJECT.
- **AUTO** aktif hanya setelah dipilih owner, tersimpan persisten dengan audit trail, dan mematuhi gerbang yang identik dengan MANUAL.
- Pemilik bisa ubah mode melalui /kontrol. Semua button hanya merespons whitelist Telegram user/chat yang diotorisasi.
- Protective SELL yang telah diotorisasi oleh kebijakan risiko harus tetap memiliki jalur keselamatan walaupun mode BUY MANUAL; transaksi SELL dan alasan dicatat serta dinotifikasi.
- Command target: /start, /status, /market, /scanner, /ai, /buy, /positions, /sell, /balance, /profit, /history, /risk, /kontrol, /health. Semuanya terhubung ke **state runtime nyata**, tidak boleh dekoratif / dummy sukses.

### C4. Modal dan order execution
- MAIN_spendable = max(0, fresh free IDR - reserve rebuy FLOKI - reserve rebuy PEPE - outstanding commitments yang belum termasuk locked IDR).
- Pisahkan buckets MAIN, FLOKI trading/rebuy/reserve, PEPE trading/rebuy/reserve; ledger lot dan asset ownership terikat setiap entry/exit. Saldonya bukan hasil menjumlah nominal hipotetis.
- Pengisian IDR, transfer/top-up, hasil SELL dan realized profit diakui setelah verified account reconciliation; tak menghitung locked, liability, dan reservation dua kali.
- Exchange adapter mendukung signed balance, open orders, status, BUY/SELL, cancel, partial fill, min order/precision, exact all-in maker/taker fees, spread/slippage dan error handling.
- Network timeout tidak berarti order gagal: sebelum retry wajib reconcile order exchange. Idempotency key, unique execution intent dan local durable journal wajib.
- Lifecycle order BUY: intent → preflight → submit → ACK/unknown → fills/cancel → settle/position ownership. Lifecycle SELL: bot-owned position only → protective/target signal → preflight → order → partial/cancel/retry → final IDR settle.
- Never sell saldo/holding owner yang bukan milik V2. Rekonsiliasi saldo exchange, order/fill dan ledger setiap restart.
- LIVE default disabled, tidak ada shortcut mengubah flag untuk menerobos evidence. Rotasi credential yang pernah terpapar sebelum live.

### C5. Position manager dan SELL
- Track entry average fill, filled qty, fees, cost basis IDR, net ROI, unfilled qty, active orders, stop-loss, trailing, time stop, exit reason, lot ownership dan IDR settlement.
- Tier target SELL **2/8/11% NETTO**. Alokasi tier dapat dikonfigurasi setelah evaluasi; jangan asal menyatakan pasti optimal.
- Stop-loss risk-based, trailing, time-stop, protective exit, market gap/slippage dan scenario pump diperhitungkan. Tidak ada indefinite HOLD tanpa exit policy dan batas risiko.
- Protective SELL boleh rugi dan dipisahkan dari gate minimum expected profit BUY. Selesaikan order ambigu sebelum memasang exit baru.

### C6. Kinerja dan feedback
- Bukukan **realized** gross/net PnL (harian/kumulatif), unrealized terpisah, fees, spread/slippage, win/loss, profit factor, drawdown, net ROI dan reliability.
- Catat prediction vs actual untuk market signal, Risk veto, Strategy entry dan exit; gunakan replay historis/ablation untuk menguji peningkatan secara out-of-sample dan hindari look-ahead bias.
- Feedback AI tidak boleh mengubah policy risk keras atau memutasi LIVE authorization tanpa izin owner.

### C7. Ops/keamanan
- Python ber-type hints; data schema ketat; konfigurasi env tervalidasi; database transaksional (mis. SQLite WAL satu writer / PostgreSQL sesuai deployment); structured redacted logs; audit trail append-only.
- systemd, graceful shutdown, health/heartbeat, restart-safe recovery, release pin SHA, least-privilege credentials, no secrets in GitHub/Telegram/log/test fixture.
- GitHub CI menjalankan unit/contracts & changed-scope integration; staging offline/public/read-only harus jelas terpisah dari trade uang nyata.
- Provider AI, public market dan signed private API dipisah secara akses dan scope; real provider inference berpotensi berbayar, catat biaya.

## D. KONTRAK, KESELAMATAN DAN DEFINITION OF DONE

**Kontrak minimum yang harus dibangun lebih dulu** (typed and versioned): MarketSnapshot, CandleSet, SourceReceipt, RankedCandidate, ThreeRoleAnalysis, RiskVeto, TradeProposal, OwnerDecision, CapitalSnapshot, ExecutionIntent, OrderState, Fill, PositionLot, ReconciliationReport, RealizedPnL.

Setiap message lintas modul membawa request/trace_id, pair, timestamps, candle-close (jika relevan), source evidence, schema version dan idempotency key bila dapat menyebabkan order. Data tidak sah ditolak sebelum provider/eksekusi.

**Hard invariants yang tidak boleh ditawar**:
1. Tak ada BUY saat Risk NO_TRADE atau source tidak terverifikasi.
2. Tak ada order saat free spendable IDR tidak terbukti fresh dan ledger tidak reconcile.
3. Tak ada duplikasi order dari retry, restart, callback atau race.
4. Tak ada SELL tanpa bot-owned lot/fill lineage dan batas qty yang valid.
5. Tak ada status Final ketiga bagi owner; semua failure proposal = REJECT.
6. MANUAL PASS single-use/expiry dan tidak pernah bypass gate.
7. AUTO dan MANUAL identik dari segi risk/execution safety.
8. Realized profit hanya dari exchange-confirmed fills/settlement, bukan virtual PnL.
9. No concurrent LIVE executors pada akun yang sama tanpa global coordination.
10. Kill-switch/protective risk action tetap bekerja dan tidak dihapus demi hanya dua label Final.

**Definition of Done modul:** kode ter-commit dan terimport di jalur utama + contracts valid + tes scoped PASS + bukti runtime sesuai klaim + ledger/checkpoint terbarui. Modul tunggal yang tidak dipanggil bukan DONE.

**Definition of Done LIVE:** koneksi & market/freshness terverifikasi, 3 AI valid, telegram callbacks benar-benar terkonsumsi sekali, permission akun/saldo/protected cash terbukti, BUY→SELL order/fill ownership dan recovery aman, deterministic Final+Preflight+Executor dibuktikan, credential aman, satu runtime service dipasang, owner explicit scoped GO. Pilot LIVE berbatas nominal dan risiko hanya setelah prasyarat ini; jangan mengklaim LIVE dari VIEW/PAPER.

## E. ROADMAP IMPLEMENTASI TUNGGAL

| ID | Deliverable dan bukti minimum | Status |
| --- | --- | --- |
| M00 | Guideline & milestone kanonis, repo private dan link README/AGENTS | SPECIFIED |
| **M01** | Foundation runnable: package layout, config fail-closed, typed contracts, DB, 1 orchestrator, smoke & tests | **NEXT** |
| M02 | Indodax public adapter + closed candles/trades/orderbook; provenance, coverage, timestamps; satu public live observation | NOT_STARTED |
| M03 | G1 scan all IDR → deep 20–40 → rank top 1–2; technical/microstructure signals, BTC regime dan historical scoring | NOT_STARTED |
| M04 | 3-role AI provider adapter, schema/evidence validation, same-candle MARKET→RISK→STRATEGY, deterministic veto | NOT_STARTED |
| M05 | Final binary policy dan Telegram MANUAL default/AUTO, owner auth, 90s one-shot proposals, callback/replay proof | NOT_STARTED |
| M06 | Private Indodax read-only balance/orders scoped, asset buckets, fresh IDR spendable, reserve protections | NOT_STARTED |
| M07 | Preflight & execution engine, fee/precision/slippage, idempotency, unknown state, order fill/cancel/reconciliation | NOT_STARTED |
| M08 | Position + SELL 2/8/11 netto, stop/trailing, partial fill, protective SELL, settled IDR and recovery | NOT_STARTED |
| M09 | Full-scoped integrated shadow/paper path using production modules, no alternate fake engine, fault/restart matrix | NOT_STARTED |
| M10 | VPS release pinned commit, secrets/security/systemd, telemetry, owner Telegram LIVE monitoring (orders OFF) | NOT_STARTED |
| M11 | Security/ownership/atomic account cutoff verification, small controlled LIVE readiness review & owner GO | NOT_STARTED |
| M12 | Limited real order BUY→SELL→IDR pilot, verified fills/net IDR, then monitored production scale-up | NOT_STARTED |

**Gate per fase:** Jangan maju hanya karena log “PASS”. Catat bukti sesuai jenis: source/code/mock/read-only/order. Jika blocker baru relevan, pecahkan blocker itu, jangan memulai audit semua modul yang sudah diterima. Setelah satu milestone sukses, tandai DONE dan pindah ke NEXT. Kembali hanya kalau kode relevan berubah atau regression nyata ditemukan.

## F. ATURAN KERJA AGAR TIDAK MUTER-MUTER

1. **Awal setiap sesi:** baca Bagian A, B, E lalu bagian C/D yang relevan untuk milestone aktif; jangan memulai dari nol.
2. Tetapkan **satu milestone aktif** dan satu vertical slice nyata. Selesaikan, sambungkan, tes, catat, lanjut.
3. **Repository baru** sebagai sumber utama. Tidak membuat kode di VPS lalu lupa menyinkronkan; deploy hanya pinned SHA dari GitHub.
4. Buat **satu scanner resmi, satu ranking, satu AI pipeline, satu Final, satu Executor, satu Position Manager, satu ledger**; adapter modular diperbolehkan, duplikasi fungsional tidak.
5. Sebelum membuat modul baru, cari existing interface dan tentukan apakah perlu memperbaiki/menyambungkan kode yang sudah ada.
6. **Tidak mengulang tes accepted PASS** jika fungsi terkait belum berubah. Uji hanya impacted scope, regression yang relevan, dan one-time proof milestone yang belum ada.
7. Setiap hasil dibedakan: SPECIFIED ≠ CODED ≠ TESTED_OFFLINE ≠ VERIFIED_PUBLIC_ONLINE ≠ VERIFIED_PRIVATE_READONLY ≠ VERIFIED_LIVE.
8. Jangan memalsukan proof, saldo, candle atau order. Jika tidak tersedia: BLOCKED/REJECT, tampilkan sebabnya dan aksi teknis konkret.
9. Selalu tuliskan setelah perubahan: **apa yang diubah, path/modul, SHA, wiring ke mainpath, apa yang benar-benar diuji, apa yang belum, satu langkah berikutnya.**
10. Jangan memperbanyak catatan master. Update checkpoint dan roadmap **di file ini saja**. Catatan teknis spesifik boleh dekat kode jika diperlukan untuk maintenance, bukan menduplikasi ledger.
11. Jangan hapus fitur yang dipakai/rollback hanya karena terlihat mirip; bersihkan duplikat berdasarkan dependency yang dibuktikan.
12. LIVE order butuh validasi teknis dan explicit owner GO terpisah. Jangan aktifkan otomatis atas dasar “kerjakan”, hasil test mock, atau tombol Telegram PASS yang belum tersambung aman.
13. Hindari overengineering dan dokumen tanpa kode. Ketika spesifikasi cukup jelas, **implementasikan** modul berikutnya, bukan terus mengusulkan arsitektur yang sama.
14. Jika ada perubahan kebijakan owner: ubah hanya aturan terdampak di dokumen ini, implementasi, dan affected tests; jelaskan perbedaan, jangan diam-diam mengubah target.
15. Jika skill/bukti lama dari bot pertama dipakai sebagai referensi, adaptasi ulang secara selektif dan aman; jangan copy .env, key, ledger, user history, atau order state lama.

**FORMAT HANDOVER RINGKAS SAAT PINDAH CHAT** (tulis di A dan baris E terkait, bukan file baru): current SHA, milestone aktif, accepted evidence, blockers, code path utama, satu langkah berikutnya, unchanged milestones yang tidak perlu dites ulang.

## G. PRIORITAS IMPLEMENTASI PERTAMA

Pada langkah berikutnya bangun **M01** di repository ini, tanpa membuat order nyata:
- satu package Python bernama indodax_ai_bot dan satu entrypoint yang jelas;
- konfigurasi type-safe dengan default NO/NO/DRY_RUN dan secret redaction;
- kontrak MarketSnapshot, RiskVeto, TradeProposal, OwnerDecision, ExecutionIntent, Order/Fill/Position;
- persistence dan idempotency dasar, basic health/status dan shutdown;
- satu orchestrator fail-closed yang menghubungkan komponen awal;
- unit test untuk invariant inti dan eksekusi nol order saat missing evidence;
- README developer yang ringkas bila perlu, CI sesuai paket, serta update checkpoint di dokumen ini.

**Tujuan final:** bot V2 yang online, modular, dapat diaudit, memakai 3 AI, BUY/SELL/IDR yang terrekonsiliasi, dikontrol Telegram, terlindung dari double-spend/duplicate order dan hanya dapat LIVE setelah syarat keamanan lengkap.

**Dokumen ini adalah kontrak desain dan antrean kerja, bukan klaim ketersediaan fitur.**