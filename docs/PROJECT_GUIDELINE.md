# PROJECT GUIDELINE & MASTER CHECKPOINT
## INDODAX AI BOT V2

**Repository:** dvoriano/indodax-ai-bot-v2 (PRIVATE)  
**Baseline:** 26 Oktober 2026  
**Canonical source:** dokumen ini. **GitHub = source of truth; VPS = environment pengujian/runtime; Indodax = sumber data saldo, order, dan fill nyata.**

> **BACA DULU SETIAP CHAT / SESI CODING:** Bagian A (checkpoint), B (keputusan tetap), C (arsitektur), D (kontrak dan keselamatan), E (roadmap), F (cara kerja). Tidak perlu meninjau ulang arsip panjang ataupun mengulang tes PASS jika fungsi tidak berubah.

## 0. IRON RULES — WAJIB, BUKAN OPSIONAL

**Perintah owner:**

> **MULAI PEKERJAAN.** Pertama susun arsitektur final, kontrak data, struktur repository, dan daftar modul utama. **Kemudian langsung implementasikan** sistem bertahap sampai satu jalur trading terintegrasi. Di setiap tahap **wajib tampilkan**: (1) kode atau modul yang benar-benar dibuat, (2) sambungannya ke jalur utama, (3) hasil pemeriksaan yang sudah terbukti, (4) bagian yang masih belum berfungsi, (5) satu langkah implementasi berikutnya. **Prioritaskan kode berjalan, integrasi nyata, pengamanan uang, dan kualitas keputusan, bukan dokumentasi berlebihan.**

**10 aturan eksekusi besi:**
1. **DAILY SCALPING IS THE PRODUCT.** Bot ini *bukan* swing bot, holdings bot, news bot atau proyek riset AI. Semua komponen mengutamakan peluang scalping spot intraday yang dapat ditutup dan dikonversi ke IDR.
2. **CODE FIRST, MAINPATH FIRST.** Selesai mendesain sekali, implementasikan vertical slice nyata; scanner → tiga AI → Final → preflight → order → SELL → IDR harus tersambung dalam satu runtime. Modul stand-alone tanpa caller runtime = NOT DONE.
3. **SINGLE SOURCE OF TRUTH.** Hanya dokumen ini sebagai guideline + checkpoint/roadmap/status; jangan bikin dokumen master atau daftar milestone duplikat.
4. **ONE IMPLEMENTATION PER CAPABILITY.** Satu scanner, satu candidate ranking, satu AI pipeline 3 role, satu Final, satu Executor, satu Position Manager, satu ledger. Adapter/provider boleh berbeda, core logic jangan kembar.
5. **NO REPEAT PASS.** Accepted evidence disimpan dengan commit SHA. Tes ulang hanya saat kode/dependency berkaitan berubah, ada regresi nyata, atau acceptance belum terbukti.
6. **EVIDENCE BEFORE CLAIMS.** Bedakan CODED / TESTED_OFFLINE / PUBLIC_ONLINE / PRIVATE_READONLY / PAPER / LIVE. Jangan menyebut live jika hanya mock, Telegram view, atau provider research.
7. **FAIL CLOSED WITH REAL MONEY.** Telegram PASS dan AUTO mode tak boleh bypass fresh source, AI Risk veto, spendable IDR, owned positions, fee/precision, Final+Preflight dan execution authorization. Live BUY/SELL butuh explicit scoped GO owner setelah proof.
8. **SCALP FAST, NOT RECKLESS.** Jangan memperlambat entry dengan model berulang atas candle yang sama; cache source-bound hasil tiga AI per unique closed candle jika layak. Tetapi jangan menerima stale price, spread berubah, quote invalid, over-budget slippage atau missing fill.
9. **NO FORCED TRADES OR GUARANTEED PNL.** Target Rp100.000 netto realized/hari hanyalah sasaran; bot boleh tidak trade dan harus menjaga drawdown/risiko. Stop-loss, trailing, protective SELL tetap wajib sekalipun hasil akhir negatif.
10. **EVERY TURN FINISH WITH 5-PART REPORT.** (1) kode/files aktual, (2) wiring mainpath, (3) proof dan SHA, (4) masih pending/blocker, (5) persis satu next action. Setelah selesai pindah milestone, jangan berkutat di satu audit berulang.

**Jika instruksi implementasi bersaing dengan keselamatan order**, keselamatan tidak dibypass. Implementasikan jalur code-to-runtime yang aman sampai gate memungkinkan Live GO.

## 0A. DAILY SCALPING ENGINE — MISI INTI BOT

Bot beroperasi **24/7 untuk memantau pasar**, tetapi hanya trading pada kesempatan yang memenuhi seluruh gerbang. *Daily* berarti strategi scalping intraday dan metrik laba per hari kalender **Asia/Jakarta**, bukan kewajiban entry tiap hari.

**Siklus harian yang wajib diimplementasikan dan dibuktikan:**
1. **Scan berulang berbasis event/candle:** pembaruan market sepanjang hari; dorong evaluasi kandidat ketika **candle 1m sudah close**, kombinasikan 5m/15m dan konteks BTC. Jangan mengirim analisis satu candle berkali-kali karena polling.
2. **Scan all → top 20–40 deep → max 1–2:** analisis spread, volume, liquidity, buyer/seller flow, impuls momentum, breakout, surge-pullback, order book, trend; tentukan peluang paling layak tanpa memaksa dua posisi.
3. **Strict freshness:** data source, pair, candle-close, bid/ask, volume, trade coverage, G1 provenance dan snapshot tiga AI terikat. Freshness/latency thresholds harus dikonfigurasi dan diukur; proposal kedaluwarsa saat bursa bergerak/coverage gagal, tak perlu menunggu maksimum TTL Telegram 90 detik.
4. **AI MARKET→RISK→STRATEGY:** 3 role benar-benar menerima market evidence yang sama dan membuat analisis *scalping entry/exit*, bukan ulasan pasar generik. Risk dapat menolak entry; Strategy wajib menyediakan sizing dan exit plan.
5. **Qualify BUY:** proyeksi laba **≥Rp10.000 net DAN ≥2% net ROI** sesudah all-in fees, spread, slippage; capital verified, max slots, no overlap/duplicate proposal, valid precision dan bot-owned constraints.
6. **Owner Final:** MANUAL default PASS/REJECT one-shot; AUTO via Telegram setelah diaktifkan. Kedua mode melalui gerbang terakhir identik dan consent LIVE yang terpisah.
7. **SELL manager aktif terus:** posisi filled tidak dibiarkan tanpa rencana exit; target **2% / 8% / 11% NET**, trailing/stop-loss, time-stop dan protective exit yang mengutamakan mitigasi risiko. **Hindari overnight HOLD** bila dapat ditutup aman, tapi jangan memalsukan kemampuan keluar atau menjual tanpa ownership.
8. **Close loop IDR:** periksa order/fill/partial/cancel/restart, net settled IDR dan ledger realized PnL. PnL harian berdasarkan fill dan settlement nyata, timezone WIB; reset agregat hari baru *tidak menghapus* riwayat/posisi.
9. **Optimize from evidence:** simpan alasan kandidat dipilih/ditolak, peluang yang dilewatkan, success/failure pattern, latency, fees, realized vs projected net ROI; ablation/out-of-sample untuk tuning, tanpa mengubah hard risk secara otonom.

**Acceptance khusus scalping** (baru bisa DONE setelah terbukti): satu siklus scanner closed 1m nyata → ranking real → trio model valid → MANUAL/AUTO Final → preflight source+balance → accepted Indodax order → matched BUY fills → managed protective/target SELL → matched SELL fills → profit bersih nyata dan IDR settlement. Sebelum Live GO, buktikan end-to-end deterministic/offline/PAPER tanpa klaim order nyata. Untuk M01, cukup **satu no-trade vertical slice fail-closed yang benar-benar runnable**, bukan menyatakan kemampuan trading sudah ada.

## A. CHECKPOINT PROYEK (SATU-SATUNYA STATUS KANONIS)

| Item | Status awal |
| --- | --- |
| Sasaran | **DAILY SCALPING INTRADAY 24/7** sebagai misi inti. Membangun bot spot scalping Indodax V2 dari repository baru dengan satu jalur online G1 → 3 AI → keputusan → execution → position/SELL → IDR dan kontrol Telegram. |
| Implementasi | **M01 CODED & TESTED_OFFLINE (7/7 local PASS)**. Foundation CLI/single orchestrator/journal is runnable. Tidak ada scanner market, 3 paid AI, private API, Telegram trading, ataupun order nyata. GitHub Actions workflow run 40296427169 **FAIL before steps** (0 langkah dieksekusi), penyebab belum terverifikasi; bukan kegagalan kode tes yang terbukti. |
| Milestone aktif | **M02: Indodax PUBLIC market source** (real 1m closed candle + trade/quotes source receipt); sambungkan ke single orchestrator tanpa mengaktifkan order. |
| Milestone selesai | M00 guideline. **M01:** commit `b73511e38ec863bdc92618426a15e8e54371454d`, 10 file baru, Python local compile & **7/7 unittest PASS**, CLI --once/--health PASS dari salinan sumber; default MANUAL/NO/NO/DRY_RUN, persisted REJECT no-data. Source repo diverifikasi tersedia di main; workflow GitHub belum berhasil memulai job. |
| Live trading | **OFF / TIDAK DIIJINKAN** sampai seluruh kriteria release terverifikasi dan ada persetujuan owner yang terpisah dan eksplisit untuk order uang nyata. |
| Bukti penerimaan | Catat pada tabel roadmap: SHA, tes yang lulus, lintasan runtime yang terbukti, keterbatasan, dan langkah berikutnya. |
| Satu langkah berikutnya | **Implementasikan M02** dengan public Indodax market adapter; buktikan source timestamps, closed 1m, quotes/trade coverage, dan sambungkan satu source receipt asli ke orchestrator. Jangan melompat ke trading. |

**Definisi status yang wajib dipakai:** NOT_STARTED, CODED, TESTED_OFFLINE, VERIFIED_PUBLIC_ONLINE, VERIFIED_PRIVATE_READONLY, VERIFIED_PAPER, VERIFIED_LIVE, BLOCKED. Gunakan status paling tinggi yang benar-benar didukung bukti, jangan lompat dari mock ke LIVE. Tanpa bukti, tetap CODED atau BLOCKED.

Setiap checkpoint setelah perubahan cukup memperbarui bagian A dan tabel E. **Jangan membuat file ledger/handover/status/checklist paralel.**

## B. KEPUTUSAN PRODUK YANG TETAP

| Area | Kebutuhan final |
| --- | --- |
| Platform | Indodax spot, modal MAIN dalam IDR, Python di Ubuntu VPS, kode di GitHub, kontrol Telegram privat. |
| Strategi | **SCALPING HARIAN sebagai satu-satunya fokus utama**, monitoring 24/7, entry berbasis closed-candle 1m disaring 5m/15m/BTC, alur IDR → BUY → SELL → IDR, exit intraday jika layak; hindari HOLD overnight tanpa alasan, selalu ada proteksi exit. |
| Profit | Target **Rp100.000 netto terealisasi per hari kalender WIB (Asia/Jakarta)**, bukan jaminan atau kewajiban bertransaksi. PnL virtual dan unrealized tidak dihitung. |
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

**Arsitektur dan antarmuka disusun satu kali, lalu segera diimplementasikan; review arsitektur ulang hanya jika fungsi/risiko material berubah. Jalur ini dirancang khusus untuk scalping harian berlatensi terukur dan exit cepat, bukan holding jangka panjang.**


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

### C0. Repository structure: implemented versus target

**Sudah tersedia pada M01 (GitHub):**

```text
indodax_ai_bot/
  __main__.py        # main CLI yang berjalan
  config.py          # default safe; LIVE selalu off pada M01
  contracts.py       # source, trade, risk, fills, owned lots
  policy.py          # scalping floors + sell tier parameters
  journal.py         # persistent idempotent decision record
  orchestrator.py    # SINGLE application orchestration point
tests/test_m01_foundation.py
.github/workflows/ci.yml
pyproject.toml
docs/PROJECT_GUIDELINE.md   # canonical roadmap/checkpoint
AGENTS.md                   # AI coding rules (bukan master kedua)
README.md
```

**Modul yang direncanakan, BELUM ADA dan tidak boleh dinyatakan berfungsi:** public data adapter (M02), G1 scanner/ranking/technical (M03), provider tiga AI (M04), Final/Telegram (M05), account/capital (M06), exchange executor/reconciliation (M07), owned position/SELL lifecycle (M08), source-bound end-to-end integration (M09–M12). Implementasikan masing-masing di dalam paket yang sama dan **hubungkan ke `DailyScalpingOrchestrator`**, jangan membangun runtime paralel. Perbaiki kebutuhan struktur bila ada bukti perubahan nyata; jangan buat banyak modul kosong sejak awal.

**Bukti M01:** lokal Python compile dan 7/7 unittest PASS; CLI `--once` menghasilkan `REJECT / G1_SOURCE_NOT_CONNECTED / execution_allowed=false`; `--health` melaporkan `DAILY_SPOT_SCALPING`, `MANUAL`, `DRY_RUN`. Tidak ada exchange connection atau order. GitHub workflow gagal *sebelum steps*, jadi GitHub CI masih pending investigasi layanan/runner, tidak dianggap test-failure source.


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
| M00 | Guideline & milestone kanonis, repo private dan link README/AGENTS | DONE (GitHub docs) |
| M01 | Foundation runnable: `indodax_ai_bot/` config/contracts/policy/journal/orchestrator/CLI; SQLite rejects persistently; compile, unit tests, CLI local | **TESTED_OFFLINE, 7/7 local PASS, SHA b73511e3; GitHub Actions FAIL pre-step (CI NOT VERIFIED)** |
| **M02** | Indodax public adapter + closed candles/trades/orderbook; provenance, coverage, timestamps; satu public live observation | **NEXT: NOT_STARTED** |
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

**IRON RULE REPORT WAJIB PER TAHAP:** (1) kode/modul dibuat, (2) sambungan mainpath, (3) pemeriksaan terverifikasi, (4) belum berfungsi, (5) satu langkah berikutnya. Dilarang berhenti hanya pada rencana atau mengulang laporan lama.

**FORMAT HANDOVER RINGKAS SAAT PINDAH CHAT** (tulis di A dan baris E terkait, bukan file baru): current SHA, milestone aktif, accepted evidence, blockers, code path utama, satu langkah berikutnya, unchanged milestones yang tidak perlu dites ulang.

## G. PRIORITAS IMPLEMENTASI PERTAMA

**M01 foundation telah dibuat** di repository ini (SHA `b73511e3`) dan diuji secara lokal. Berikut isi/hasil scope M01:
- `indodax_ai_bot` + `python -m indodax_ai_bot --once/--health` (single CLI);
- konfigurasi `Settings` fail-closed default NO/NO/DRY_RUN dan MANUAL (tanpa menyentuh exchange/secrets);
- kontrak typed MarketSnapshot, RiskVeto, TradeProposal, OwnerDecision, ExecutionIntent, OrderState, Fill, PositionLot;
- SQLite decision journal idempotent dan health/status CLI; restart/replay REJECT 1/1 diuji;
- satu `DailyScalpingOrchestrator`: no G1 → Final REJECT persisten, execution_allowed=false;
- 7/7 local unittest PASS, CLI health/once PASS, compile PASS; bukan verifikasi VPS atau exchange;
- workflow GitHub Actions disimpan, tetapi run #1 `40296427169` gagal sebelum steps sehingga CI belum accepted; jangan menyebut PASS GitHub CI.

**Tujuan final:** bot V2 yang online, modular, dapat diaudit, memakai 3 AI, BUY/SELL/IDR yang terrekonsiliasi, dikontrol Telegram, terlindung dari double-spend/duplicate order dan hanya dapat LIVE setelah syarat keamanan lengkap.

**Dokumen ini adalah kontrak desain dan antrean kerja, bukan klaim ketersediaan fitur.**