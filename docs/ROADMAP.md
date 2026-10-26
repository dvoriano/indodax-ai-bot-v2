# ROADMAP & EXECUTION CHECKLIST — INDODAX AI BOT V2

**Pemilik:** dvoriano/indodax-ai-bot-v2, branch main  
**Tanggal baseline:** 26 Oktober 2026 (WIB)  
**ATURAN YANG MENGIKAT:** [PROJECT_GUIDELINE.md](PROJECT_GUIDELINE.md) terutama 0, 0A, B, C, D, E, F.  
**Fungsi file ini:** satu-satunya **tracker checklist dan handover**. Guideline mengatur spesifikasi/keputusan; roadmap mencatat pekerjaan/bukti. Jika tidak sejalan, guideline menang. Jangan membuat roadmap/checkpoint/handover kedua.

> **MISI BOT = DAILY SPOT SCALPING**, pemantauan 24/7, keputusan berdasarkan candle 1m closed dengan konteks 5m/15m dan BTC, akhir MAIN di IDR. Tidak boleh memaksa transaksi untuk mencapai target Rp100.000 **NETTO REALIZED per hari WIB**. Tidak boleh mengubah kebijakan dari guideline tanpa keputusan owner.

## 1. START HERE: HANDOVER SINGKAT SETIAP PINDAH CHAT

| Bidang | Checkpoint terkonfirmasi / status kerja |
| --- | --- |
| **Tahap aktif (satu saja)** | **M03-B FAILED_VPS → FIX CODED, RERUN PENDING.** Pada SHA `44ec7c43` VPS menjalankan 33 tes: 28 OK, 5 ERROR karena `NameError: name 'c' is not defined` di `_last_continuous`; test script berhenti sebelum `--deep-once`. Patch spesifik tersedia di GitHub dan perlu retest. M03-A sebelumnya PASS `bdcd9f5`. M03-B sekarang mengukur candle 1/5/15m, sampled flow dan book evidence pada pilot maksimal 2 pair. |
| Terakhir dibangun | `scanner.py` MAIN exclusion PEPE/FLOKI/USDT/USDC; `public_market.py` typed observed 1m/15m history, sampled closed-minute trade flow dan top5 depth; `deep_analysis.py` EMA8/EMA21/RSI14, volume, spread, 5m/15m, and source continuity checks; orchestrator dan CLI `--deep-once`; 6 M03-B tests awal: 5 ERROR pada `44ec7c43`; 1 regression baru untuk a/b candle checks sehingga total M03-B 7 test, patch SHA `22f8acad`, PASS belum diverifikasi. |
| Mainpath accepted | **M03-A VPS:** CLI `--scan-once` → orchestrator → `DailyPairScanner` → SQLite Journal → REJECT. **M03-B CODED, belum VPS:** CLI `--deep-once` → orchestrator → same G1 scanner → bounded deep 2 → IndodaxPublicMarket.observe → evidence diagnostic → Journal REJECT. |
| Accepted proof M03-A | Owner VPS output `bdcd9f5`: 9/9 tests PASS (0.069s), 398 IDR, 312 eligible, 86 excluded, shortlist30, watchlist2 = USDT/IDR & BTC/IDR **sebelum pemisahan MAIN**, `M03A_EXIT_CODE=0`, epoch 1793010913; `g1_issuer_verified=false`, `execution_allowed=false`, Final REJECT. M02 public BTC/IDR probe juga accepted di `7002387`. |
| Blocker yang masih terbuka | **M03-B changed-scope tests VPS dilakukan tetapi FAILED:** 33 tests, 5 ERROR dari satu `NameError` (SHA `44ec7c43`, exit1). Patch variabel `c` → verifikasi interval `a` & `b` sudah committed di `22f8acad` namun belum diuji VPS; `--deep-once` belum jalan karena `set -e` menghentikan proses. Deep hanya 2 pair dalam pilot, bukan analisis lengkap 20–40. M02 trade coverage belum lengkap/verified, source owner G1 belum diotorisasi, AI belum terhubung, CI GitHub masih gagal 0-step. |
| Status uang nyata | **LIVE OFF.** Tidak ada private Indodax account/trading, AI berbayar, order ataupun realized PnL pada bot V2. |
| Bot lama / keputusan VPS | **Pembersihan VPS lama DITUNDA.** Semua service, file, API credentials dan kunci bot lama tidak disentuh. V2 di `~/indodax-ai-bot-v2`, key khusus V2. Dua executor tidak boleh trading akun yang sama tanpa ownership/cash coordination. |
| **SATU NEXT ACTION** | **M03-B: pull patch SHA `22f8acad` atau terbaru di VPS, jalankan scoped `test_m03b_deep.py` (termasuk regression baru); hanya jika PASS jalankan satu `--deep-once` publik.** Jangan ulang seluruh modul accepted yang tidak terdampak. Periksa skor observasi atau alasan fail-closed, source epoch dan Final REJECT; perbaiki hanya jika ada temuan. |

**Cara lanjut chat baru:** baca guideline 0/0A/B/E/F → tracker ini checkpoint 1 → milestone 3 → evidence 4. Jangan audit ulang accepted M03-A selama fungsi tidak berubah; pengujian terarah tetap perlu untuk **perubahan fungsi scanner dan adapter** sejak `bdcd9f5`.

## 2. LEGENDA CHECKLIST & BUKTI

- **[x] Selesai/diperiksa:** aksi spesifik telah dilakukan dan memiliki rujukan kode/dokumen/bukti. **Bukan** otomatis keseluruhan milestone DONE.
- **[ ] Belum terpenuhi/terverifikasi:** pekerjaan masih harus dibuat, disambungkan atau dibuktikan.
- **Tahap DONE** hanya jika seluruh acceptance yang wajib di tahap itu terbukti. Jika hanya sebagian, sebut **PARTIAL** secara jelas.
- Status bukti sesuai guideline: NOT_STARTED → CODED → TESTED_OFFLINE → VERIFIED_PUBLIC_ONLINE → VERIFIED_PRIVATE_READONLY → VERIFIED_PAPER → VERIFIED_LIVE. Jangan menaikkan tahap berdasarkan test mock.
- **Bukti minimum per centang:** path kode/commit SHA, lintasan fungsi/orchestrator, jenis pengujian, hasil aktual dan batas yang belum terbukti. Dilarang mencentang berdasarkan rencana atau klaim tanpa bukti.
- **Tes PASS tidak diulang** kecuali fungsi/dependency berubah, muncul regresi nyata, atau bukti acceptance tersebut memang belum ada.
- Ketika milestone selesai: centang masing-masing task yang benar-benar lulus, tulis SHA dan bukti di bagian 4, ubah checkpoint bagian 1, lalu pindah ke **satu** milestone berikutnya. Perubahan spesifikasi owner dilakukan di guideline terlebih dahulu.

## 3. CHECKLIST PER MILESTONE (M00–M12)

### M00 — Fondasi arah, Iron Rules dan dokumen acuan
**Status: DOCUMENTATION COMPLETE. Bukan bukti trading.**
- [x] Repository privat independen dvoriano/indodax-ai-bot-v2 dibuat.
- [x] PROJECT_GUIDELINE.md menjabarkan arsitektur tunggal, kontrak, iron rules dan misi DAILY SCALPING.
- [x] AGENTS.md dan README.md mengarahkan coding agent ke guideline.
- [x] Kebijakan risiko inti ditetapkan: MAIN IDR; maksimal 1–2 pair; target minimal proyeksi BUY Rp10.000 net DAN ROI 2% net; SELL 2/8/11% net; tiga AI; final PASS/REJECT.
- [x] Tracker roadmap tunggal ini dipisahkan dari policy guideline atas permintaan owner.
**Bukti:** [guideline](PROJECT_GUIDELINE.md), [agent rules](../AGENTS.md), [README](../README.md). Tidak ada eksekusi Indodax.

### M01 — Python runtime foundation (kode sudah ada, verifikasi belum lengkap)
**Status: PARTIAL, TESTED_OFFLINE menurut bukti lokal pada guideline.**
- [x] Paket Python dan entrypoint python -m indodax_ai_bot dibuat; file pyproject.toml.
- [x] Konfigurasi Settings default MANUAL / NO / NO / DRY_RUN dengan pengaktifan LIVE ditolak pada M01.
- [x] Kontrak SourceReceipt, MarketSnapshot, RiskVeto, TradeProposal, OwnerDecision, ExecutionIntent, OrderState, Fill, PositionLot.
- [x] ScalpingPolicy dibuat dengan batas BUY minimum dan SELL tier 2/8/11% netto.
- [x] SQLite DecisionJournal mencatat REJECT idempotent, unik per trace, restart persistence.
- [x] Satu DailyScalpingOrchestrator tersambung ke CLI → Journal, fail-closed tanpa sumber G1.
- [x] Tes lokal 7/7, compile, --once dan --health dicatat PASS pada guideline; **bukan bukti CI/VPS**.
- [x] File CI GitHub Actions dibuat dan tersimpan di repository.
- [ ] **GitHub Actions CI sukses:** run terkait justru gagal 0 steps; tetap terbuka hingga penyebab CI dipastikan dan ada PASS yang benar.
- [ ] M01 memiliki bukti eksekusi lewat runner CI (belum); tidak perlu mengulang tes lokal yang sudah diterima tanpa perubahan fungsional.
**Bukti kode:** [commit b73511e3](https://github.com/dvoriano/indodax-ai-bot-v2/commit/b73511e38ec863bdc92618426a15e8e54371454d), [test file](../tests/test_m01_foundation.py). **Batas:** belum ada scanner, AI, akun, Telegram, orders. M01 bukan VERIFIED_LIVE.

### M02 — Public Indodax Market Data & Source Trust (PUBLIC OBSERVATION ACCEPTED)
- [x] SSH ED25519 khusus V2 dibuat di VPS dan public key dikeluarkan (fingerprint tertera di checkpoint); **tidak termasuk bukti akses GitHub**.
- [x] **SSH auth + read access repo V2 terbukti di VPS** melalui `git ls-remote` identitas `~/.ssh/indodax_ai_bot_v2`, HEAD `3d25703925da95f8f72e59586c5aafb4f610ecf1`. Jenis permission key (read-only checkbox) belum dicek langsung, sehingga klaim terbatas pada keberhasilan baca.
**Status: VERIFIED_PUBLIC_ONLINE untuk satu observasi BTC/IDR nyata; trade history coverage dan issuer masih fail-closed / pending.**
- [x] Kode Indodax public-only API adapter daftar IDR pairs, ticker, bid/ask, trades, depth, OHLC 1m/15m **sesuai dokumentasi resmi**; belum terbukti semua endpoint sukses HTTP live.
- [x] Normalisasi `ticker_id=btc_idr`, `id=btcidr`, symbol `BTCIDR`, harga Decimal, server_time milliseconds → epoch seconds (UTC); WIB khusus reporting, jangan ubah candle timestamp. Verifikasi precision fee/order tetap tahap M07.
- [x] Parser closed 1m dan native closed 15m; 5m agregasi **hanya bila lima candle 1m lengkap**. Candle ongoing dibuang; zero-volume yang benar-benar ada di response dipertahankan, tidak sintesis candle yang hilang.
- [x] Buat SourceReceipt 1m close/observed/provenance `INDODAX_PUBLIC_OHLC_V2_UNATTESTED`, `verified=False`, snapshot quotes, visible trades/depth dan `trade_history_complete=False`; return envelope ke mainpath. Bukti eksistensi schema ≠ trusted G1 issuer.
- [ ] **FULL COVERAGE/CONTINUITY** trade history per 1h/15m belum diverifikasi; page-limited REST secara konservatif selalu `trade_history_complete=False`. Parser sudah menolak incomplete 5m, stale clock, ticker, candle, invalid trades/book; tetapi belum membuktikan coverage dan trade→candle join yang lengkap.
- [x] Sambungan **CLI `--public-once` → DailyScalpingOrchestrator → IndodaxPublicMarket → persisted REJECT** teruji dengan fixture berbentuk API resmi. Tidak ada private API, provider, order, atau path approval positif.
- [x] **9/9 M02 offline tests PASS**, 16/16 total pada SHA terdahulu, hash isi remote cocok dengan local. [x] 2 test fixture baru untuk CLI `--verify-public` dibuat (PASS belum diverifikasi di CI). [x] **SATU public live observation** terbukti pada VPS: `M02_EXIT_CODE=0`, `PUBLIC_OBSERVED_G1_UNATTESTED`, observed epoch 1793009915. Ini bukan izin BUY.
- [x] Catatan scope/source/hasil test dan alasan `G1_PROVENANCE_NOT_VERIFIED` pada fixture ada di evidence register. [x] Pair/timestamp/hasil data publik asli BTC/IDR tercatat pada tabel handover dan evidence register.
**Observasi public M02 telah terbukti lintas timeframe di VPS.** Acceptance deep coverage/issuer tetap terbuka; tidak boleh dianggap izin BUY. **Bloker akses sebelumnya sudah terselesaikan lewat identitas V2 khusus:** `git ls-remote` PASS di VPS; clone dan verifikasi HTTP public M02 SUDAH PASS pada satu observasi. **Owner memutuskan tunda pembersihan VPS**; siapkan lingkungan V2 terpisah tanpa menghapus/mematikan service lama. Bootstrap read-only SUDAH dijalankan dan menghasilkan exit 0. Gunakan SSH deploy key read-only unik dan pastikan `git ls-remote` berhasil sebelum memulai probe. **M02 public observation accepted.** Tidak perlu mengulang probe identik jika fungsinya tidak berubah. Selanjutnya M03-A scanner top IDR dan tes scoped. 

### M03 — G1 Daily Scalping Scanner, Ranking & Microstructure (AKTIF)
**Status: M03-A VERIFIED_PUBLIC_ONLINE (9/9 PASS pada `bdcd9f5`); M03-B FAILED_VPS pada `44ec7c43` (33 tests, 5 NameError), patch targeted `22f8acad` CODED, awaiting rerun.**
- [x] M03-A real source: `/api/pairs`, `/api/server_time`, `/api/ticker_all` dalam satu `--scan-once`, 398 pair IDR, filter awal 312, ranking preliminary 30 dan watchlist max2, real source.
- [x] M03-A tes scoped **9/9 PASS pada VPS**, exit0, Journal Final REJECT, G1 issuer false. Bukti sebelum perubahan filter MAIN yang baru; jangan klaim perubahan terbaru sudah PASS.
- [x] **M03-B CODED:** `MAIN_EXCLUDED_PAIRS` = PEPE/FLOKI/USDT/USDC, untuk mencegah pembauran dana MAIN vs reserve/rebuy asset. Saldo riil tetap belum dibaca.
- [x] **M03-B CODED:** tambah bukti `PublicObservation` berupa closed 1m/15m history, sampled closed-minute buy/sell trade value, top5 bid/ask depth notional, semuanya tidak diklaim sebagai cakupan trade lengkap.
- [x] **M03-B CODED:** `deep_analysis.py` indikator EMA8/21, RSI14, volume ratio, 5m/15m trend, spread, imbalance, sample flow, source continuity + stale/candle receipt checks. Skor hanya **diagnostik**, bukan ROI atau BUY signal.
- [x] **M03-B WIRED CODE:** `--deep-once` → one orchestrator → existing scanner shortlist → bounded max2 `observe` calls → `deep_scan` → Journal Final REJECT.
- [x] **M03-B FAILED TEST EVIDENCE:** VPS SHA `44ec7c43`, 33 tes, 5 errors semua pada `_last_continuous()` akibat nama `c` tak didefinisikan. Script stop sebelum live-public deep probe; exit1. Tes lainnya OK.
- [x] **M03-B TARGETED FIX CODED:** SHA `22f8acad`, periksa interval `a.interval` dan `b.interval` serta continuity `b.start-a.start`. Tambah 1 test regression candle timeframe mismatch; total 7 tes M03-B, PASS baru belum terbukti.
- [ ] **M03-B SCOPED RETEST:** jalankan hanya M03-B test yang berubah pada VPS SHA `22f8acad` atau lebih baru. M03-A/M02 sebelumnya PASS kecuali impacted regression relevan dari perubahan deep_analysis.
- [ ] **M03-B PUBLIC VPS PROBE:** satu `--deep-once` dengan hasil source-bound 1m/5m/15m, source untrusted, no trade, REJECT, tulis SHA/reasons di evidence register.
- [ ] **M03-C:** tingkatkan deep 20–40 menggunakan scheduler/rate budget. Indodax batas publik 180 request/menit, hindari 30 × multi-call sekaligus. Tambah BTC regime dan true 1/5/15m source joins.
- [ ] Validasi minimum notional per pair, listing/suspend risk dan BOT capital ownership before qualified G1 proposals.
- [ ] Trade/candle coverage terverifikasi, G1 provenance issuer yang sah, antarmuka ke G2 tiga AI tanpa duplikasi scanner dan 1–2 final candidates yang dibuktikan.
- [ ] Replay historical ablation, volume/imbalance/false-breakout evidence dan look-ahead prevention secara scoped.
**Acceptance penuh M03 belum tercapai.** M03-A public scanning verified, M03-B feature analysis masih observasional, G1 source trust dan G2 belum terhubung. Tidak boleh BUY.

### M04 — 3 AI MARKET → RISK → STRATEGY
**Status: NOT_STARTED. Bergantung G1 sourced candidates.**
- [ ] Satu AI pipeline berurutan MARKET → RISK → STRATEGY; tidak membuat AI keempat.
- [ ] Provider adapter configurable (Sumopod/model dapat diganti); pastikan ketersediaan/biaya model sebelum paid calls.
- [ ] Ketiga role menggunakan snapshot pair + candle yang identik, closed, fresh, berprovenance; reject stale/missing.
- [ ] MARKET membaca BTC/trend/volume/flow/sinyal teknikal dengan evidence yang bisa ditelusuri.
- [ ] RISK veto ALLOW/NO_TRADE/EXIT_REVIEW; veto tak bisa dibatalkan oleh Strategy/owner.
- [ ] STRATEGY menghasilkan entry/qty/exit plan, biaya dan projected net profit/ROI, konsisten dengan veto.
- [ ] Strict typed response validation, timeout/retry terbatas, invalid JSON/provider errors → REJECT; dedupe per candle.
- [ ] Integrasi tiga AI sebenarnya ke orchestrator; buktikan provider/test status secara terpisah, bukan fake model sebagai LIVE.
**Acceptance:** valid source-bound triple-role decisions tersedia, tanpa klaim boleh mengirim order.

### M05 — Final Binary + Telegram MANUAL/AUTO
**Status: NOT_STARTED.**
- [ ] Final Decision owner-facing **hanya PASS / REJECT**; fault/no-trade/veto → REJECT.
- [ ] Default MANUAL; owner Telegram authenticated dan whitelisted, tombol PASS/REJECT untuk proposal valid.
- [ ] Telegram /start, /status, /scanner, /market, /ai, /buy, /positions, /sell, /balance, /profit, /history, /risk, /kontrol, /health terhubung ke state nyata.
- [ ] Proposal ID+pair+hash market/candle+entry+qty+owner, TTL maksimal 90 detik atau lebih cepat jika sumber stale.
- [ ] Callback single-use/transactional, anti-replay, one decision per proposal; REJECT/expiry membatalkan.
- [ ] AUTO bisa dipilih owner dan tetap mematuhi semua gate, audit trail perubahan mode persisten.
- [ ] PASS MANUAL tidak bypass Risk, spendable IDR, source/freshness/preflight; protective SELL memiliki jalur keselamatan tersendiri.
- [ ] Tes callback, switching, concurrency, expiry dan wiring yang nyata ke orchestrator (belum membuat order).
**Acceptance:** owner hanya mengendalikan proposal sah, tombol bukan override keamanan.

### M06 — Account, Dynamic Capital & FLOKI/PEPE Isolation
**Status: NOT_STARTED. Private endpoints memerlukan izin/scope jelas.**
- [ ] Implementasi akun signed read-only secara sempit: free/locked IDR, open orders, position ownership, waktu data.
- [ ] MAIN spendable = verified free IDR dikurangi reserved rebuy FLOKI/PEPE dan commitments yang belum termasuk locked.
- [ ] Rekonsiliasi top-up, laba/penjualan yang masuk saldo secara sah; hindari menghitung kas ganda.
- [ ] FLOKI terpisah: trading 25%, reserve 75%, hasil jual hanya rebuy FLOKI.
- [ ] PEPE terpisah: trading 30%, reserve 70%, hasil jual hanya rebuy PEPE.
- [ ] Bot-owned lot/ledger, asset ownership, outstanding liabilities, balance cutoff/freshness dan atomic reservation.
- [ ] Jangan pakai akun yang sama dengan bot lama untuk dua executor LIVE tanpa koordinasi bersama yang diverifikasi.
- [ ] Tests missing/stale/partial ledger -> NO BUY; scoped private read-only proof tanpa eksekusi.
**Acceptance:** MAIN spendable benar-benar terikat bukti akun; token owner tidak bisa dipakai bot secara tidak sengaja.

### M07 — Final Preflight & Indodax Execution Engine
**Status: NOT_STARTED, LIVE HARUS OFF sampai release gate.**
- [ ] Adapter official Indodax untuk order BUY/SELL/cancel/status/openOrders dan signed request yang benar.
- [ ] Validasi minimum notional, step/precision per pair, net maker/taker all-in fee, spread/slippage dan conservative projected net ROI.
- [ ] Satu ExecutionIntent per proposal, durable idempotency, preflight last-moment Risk/market/account/ownership recheck.
- [ ] Explicit order lifecycle: CREATED → SUBMITTED/UNKNOWN → FILLS/PARTIAL/CANCEL → SETTLED; never assume timeout = rejection.
- [ ] Reconcile exchange order/fill sebelum retry, startup recovery, duplicate BUY/SELL prevention.
- [ ] Hardlock fail-closed dengan env mode dan release authorization; tes seluruh fault path tanpa order riil.
- [ ] Sambungkan executor tunggal ke orchestrator, bukan membuat paper executor alternatif yang berbeda logika.
**Acceptance:** preflight/ledger/order path siap dinilai, **bukan** persetujuan LIVE otomatis.

### M08 — Position Manager, Protective SELL & IDR Settlement
**Status: NOT_STARTED.**
- [ ] Daftarkan posisi hanya dari matched bot-owned BUY fills, average cost IDR dan round-trip actual fees.
- [ ] TARGET SELL tier **2%, 8%, 11% NETTO**, alokasi tier configurable tanpa klaim profit pasti.
- [ ] Risk-based stop-loss, trailing, time-stop, pump protection, no unbounded HOLD.
- [ ] Protective SELL boleh realized loss; dipisahkan dari syarat Rp10.000/2% BUY.
- [ ] Partial fill/cancel/retry, qty ownership, residual lots dan guarded SELL handling.
- [ ] Restart recovery dan consistency exchange order/fill/position/wallet hingga saldo akhir IDR.
- [ ] Catat net realized PnL setiap fill tersettled, agregat harian Asia/Jakarta, pisahkan unrealized.
- [ ] Offline integration dan replay path BUY→SELL→IDR; stage LIVE proof tetap M12.
**Acceptance:** posisi tak hilang saat restart dan semua exit punya asal-usul sah.

### M09 — Mainpath End-to-End Staging (tanpa uang nyata)
**Status: NOT_STARTED.**
- [ ] Uji 1 orchestrator G1 → 3 AI → Final → Preflight → Executor hardlock → Position/SELL manager → accounting.
- [ ] Shadow/PAPER memakai core production modules, tidak menduplikasi strategi.
- [ ] Uji BUY reject, risk veto, top 1–2, stale data, manual/auto, race callbacks, funding/precision failure.
- [ ] Uji partial fill, network timeout, duplicate prevention, restart dan reconciliation.
- [ ] Pastikan PAPER profit dilabel VIRTUAL dan tidak dihitung target harian nyata.
- [ ] Evidence SHA, path, test results dan field yang benar-benar terhubung; jangan klaim Indodax live.
**Acceptance:** satu vertical slice realistis teruji; semua order actual OFF.

### M10 — VPS Online Monitoring, CI & Operational Release
**Status: NOT_STARTED; CI awal punya hambatan tercatat.**
- [ ] Selesaikan kegagalan GitHub Actions 0-step secara terarah; dapatkan pemeriksaan CI hijau dan catat bukti.
- [ ] VPS isolated dari bot lama, clone pinned SHA, runtime env secrets terpisah, service systemd tunggal.
- [ ] Connect public market, tiga AI jika diizinkan dan Telegram secara online; send owner-safe research dan health.
- [ ] Verify service startup, restart, heartbeat, Telegram authorization, error logs redacted, uptime/latency.
- [ ] Eksekusi uang nyata tetap OFF, final trading route hardlocked; jangan klaim VIEW/PAPER sebagai LIVE.
- [ ] Monitor false re-entry, loss of market freshness, API/provider failures, stop/kill behavior.
**Acceptance:** satu service online stabil tanpa memperjualbelikan aset, dengan bukti deployment pinned.

### M11 — LIVE Readiness / Risk Release Gate
**Status: NOT_STARTED.**
- [ ] Verifikasi G1 provenance issuer + full market evidence dan risk permissions (tidak bisa diubah flag saja).
- [ ] Verifikasi fresh authenticated account cutoff/reserves/liabilities, position ownership dan spendable MAIN.
- [ ] Rotasi kredensial lama yang pernah terekspos, validasi scopes, no secrets in Git.
- [ ] Pastikan tidak ada executor bot lain pada akun yang sama tanpa cross-bot coordination.
- [ ] Validasi order/fill/SELL protective path, duplicate prevention, restart recovery dan circuit breaker.
- [ ] Definisikan pilot LIVE limit (saldo maksimal dipakai, jumlah order, maksimal rugi/hari, pair, saat dan otorisasi).
- [ ] Dapatkan **GO owner eksplisit terpisah untuk order uang nyata**, setelah proof dan pembatasan pilot.
**Acceptance:** keputusan otorisasi rilis tercatat; tidak cukup hanya Telegram PASS/AUTO.

### M12 — Limited Real BUY → SELL → IDR Pilot & Production Ramp
**Status: NOT_STARTED. Tidak ada order uang nyata yang telah dilakukan di V2.**
- [ ] Jalankan hanya order pilot kecil yang sesuai GO owner dan seluruh gate.
- [ ] Dapatkan exchange-confirmed BUY order ID dan matched fills; lindungi posisi via sell path.
- [ ] Dapatkan exchange-confirmed SELL fills, fees dan final IDR settled wallet change.
- [ ] Rekonsiliasi ledger ↔ exchange ↔ capital, hitung net realized PnL aktual.
- [ ] Uji kepatuhan MANUAL/AUTO dan protective SELL di operasi riil sesuai scope tanpa membahayakan aset lain.
- [ ] Evaluasi drawdown/latency/slippage, failure rates, guardrails dan kemampuan pemulihan.
- [ ] Hanya setelah pilot aman, owner menyetujui perluasan batas modal/operasi dan monitoring harian.
**Acceptance:** benar-benar terdapat order/fill/settlement nyata yang terikat account/owner, bukan paper result.

## 4. EVIDENCE REGISTER (JANGAN DUPLIKASI TES)

| Milestone | SHA / sumber | Bukti diterima | Belum dibuktikan |
| --- | --- | --- | --- |
| M00 | Dokumen guideline, AGENTS, README yang committed pada main | Kebijakan, Iron Rules dan roadmap spesifikasi tersedia | Bot trading LIVE |
| M01 | [b73511e3](https://github.com/dvoriano/indodax-ai-bot-v2/commit/b73511e38ec863bdc92618426a15e8e54371454d); [tests/test_m01_foundation.py](../tests/test_m01_foundation.py) | Menurut guideline: 7/7 tes lokal, compile, CLI --once/--health PASS; orchestrator+SQLite memproduksi REJECT fail-closed | GitHub runner CI, sumber pasar asli, 3 AI, real execution |
| M01 CI | [run 40297122063](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/40297122063) | Dikonfirmasi job gagal sebelum menjalankan langkah apa pun (0 steps) | Alasan runner/workflow failure, CI PASS |
| M02 CODED | `public_market.py`, `orchestrator.py`, `__main__.py`, `journal.py`, `tests/test_m02_public_market.py` | Earlier 9/9 M02 + 7/7 M01 local PASS on unchanged prior scope; 2 new CLI probe tests have **no verified runner PASS yet**. API docs: https://github.com/btcid/indodax-official-api-docs/blob/master/Public-RestAPI.md | No real public observation from VPS, G1 issuer, or CI PASS; no trading |
| M02 REAL PUBLIC VPS | `7002387f` output dari user pada 26 Okt 2026 | Repo clone/pull dan market BTC/IDR public REST valid, candle 1/5/15m, 500 trade visible, 300 depth visible, exit0 dan Final REJECT | Trade history lengkap, G1 trusted issuer, BUY/order |
| M03-A VPS PASS | `bdcd9f5` owner VPS console 26 Okt 2026 | **9/9 tests PASS**, 398 pairs IDR, 312 eligible, 86 rejected, shortlist30, watchlist USDT/IDR + BTC/IDR, epoch 1793010913, exit0, Final REJECT | New MAIN exclusion not yet tested; deep source/AI/BUY absent |
| M03-B ERROR + PATCH | `44ec7c43` tested on VPS; fix `22f8acad` `deep_analysis.py` + `test_m03b_deep.py` | **33 tests run, 5 ERROR** all `NameError c` on candle continuity. Patch uses `a.interval`/`b.interval`, 1 new regression test; **no retest PASS** yet. `--deep-once` was skipped due to `set -e`. | Run M03-B scoped 7 tests on VPS, then one public deep diagnostic; G1/AI/trade not ready |
| M03-B–M12 | Belum ada acceptance | Tidak boleh dicentang atau dilabel selesai | Implementasi dan bukti stage masing-masing |

**Catatan:** Tanggal baseline tidak berarti semua isi tersertifikasi hari itu. Ketika bukti baru diverifikasi, tambahkan **satu baris** sesuai milestone. Jika hanya perubahan status, jangan memodifikasi aturan trading di guideline.

## 5. FORMAT WAJIB SETELAH SETIAP TAHAP

Tulis ringkas di akhir sesi, dan perbarui checklist + checkpoint di sini:

1. **DIBUAT:** path kode/fitur aktual dan commit SHA.
2. **TERSAMBUNG:** caller → callee → bukti jalur utama; tulis jelas jika belum.
3. **DIPERIKSA:** tes per fungsi berubah, PASS/FAIL teramati, sumber real/mock/private, reference.
4. **BELUM:** error, blocker, izin atau tahapan yang belum tercapai; jangan menulis DONE bila tidak benar.
5. **NEXT SATU LANGKAH:** satu vertical slice yang paling mendekatkan pada bot scalping harian terintegrasi.

**Syarat selesai proyek akhir:** source scanner real, top candidates, MARKET/RISK/STRATEGY, PASS/REJECT MANUAL/AUTO, authenticated capital safe, idempotent BUY/SELL, protective exit, order/fill ownership, settled IDR dan realized net PnL; semua terbukti terhubung pada satu runtime dan ada persetujuan owner untuk LIVE. Tak ada jaminan Rp100.000 laba harian.
