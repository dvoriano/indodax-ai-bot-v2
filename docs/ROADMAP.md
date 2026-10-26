# ROADMAP & EXECUTION CHECKLIST — INDODAX AI BOT V2

**Pemilik:** dvoriano/indodax-ai-bot-v2, branch main  
**Tanggal baseline:** 26 Oktober 2026 (WIB)  
**ATURAN YANG MENGIKAT:** [PROJECT_GUIDELINE.md](PROJECT_GUIDELINE.md) terutama 0, 0A, B, C, D, E, F.  
**Fungsi file ini:** satu-satunya **tracker checklist dan handover**. Guideline mengatur spesifikasi/keputusan; roadmap mencatat pekerjaan/bukti. Jika tidak sejalan, guideline menang. Jangan membuat roadmap/checkpoint/handover kedua.

> **MISI BOT = DAILY SPOT SCALPING**, pemantauan 24/7, keputusan berdasarkan candle 1m closed dengan konteks 5m/15m dan BTC, akhir MAIN di IDR. Tidak boleh memaksa transaksi untuk mencapai target Rp100.000 **NETTO REALIZED per hari WIB**. Tidak boleh mengubah kebijakan dari guideline tanpa keputusan owner.

## 1. START HERE: HANDOVER SINGKAT SETIAP PINDAH CHAT

| Bidang | Checkpoint terkonfirmasi / status kerja |
| --- | --- |
| **SATU TAHAP AKTIF** | **M04-A CODED, VPS TEST PENDING.** G1 M03-I accepted VERIFIED_PUBLIC_ONLINE on SHA `c3fe498c`: 8/8 scoped tests PASS 0.062s and real public bulk 20/20, 8 diagnostic-valid/12 zero-last-1m-volume, 83 public GET, aligned 8-pair cohort epoch1793023680, BTC DOWNTREND, preview empty, Final REJECT, exit0. |
| Mainpath saat ini | `--bulk-deep-once` → existing DailyScalpingOrchestrator → G1 scanner + shared public bulk → eligible same-minute cohort → **ThreeRoleShadow.run** → Journal Final REJECT. Tidak ada G1 trust issuer, actual provider, private Indodax execution, ataupun AI LIVE. |
| M04-A CODED | `indodax_ai_bot/three_role_ai.py` one MARKET→RISK→STRATEGY shadow pipeline, per-candle source digest binding, strict role output schema, Risk veto NO_TRADE/EXIT_REVIEW, fail-closed malformed/stale/changed source, previews with unresolved quality cautions blocked; `deep_analysis.py` adds screening timestamp/explicit untrusted flags, `orchestrator.py` and CLI expose `ai_shadow`. `tests/test_m04a_three_role_shadow.py` **10 tests CODED only**, never run VPS. |
| Bukti diterima | M03-A–I accepted for their scoped public evidence, latest M03-I 8/8 PASS on source-cohort only. Current source is `g1_verified=false`, `trade_history_complete=false`, `source_trust_verified=false`. *A local SHA digest does NOT authenticate Indodax source.* |
| Status AI dan uang | **3 AI roles CONTRACT ONLY**, provider not connected/paid. Public source must not invoke a model or create a trading proposal. LIVE OFF; `final_decision=REJECT`, `execution_allowed=false`, no BUY/SELL. |
| Remaining | M04-A tests/live-public mainpath proof; provenance/coverage to qualify G1 (trade samples still page-limited), actual Sumopod provider adapter + authorized credentials, 3 model structured API calls, config, timeout/costs, Telegram Final MANUAL/AUTO, private capital, preflight and SELL lifecycle. GitHub Actions 0-step unresolved. |
| Legacy VPS | Bot lama, its services/files/keys untouched. V2 repo separate in `~/indodax-ai-bot-v2`. |
| **SATU NEXT ACTION** | **M04-A pull latest SHA V2 to VPS; run tests `test_m04a_three_role_shadow.py` and affected `test_m03i_valid_cohort.py`; then one public `--bulk-deep-once` to prove `ai_shadow.reason` blocks 3-role provider/BUY with real G1 untrusted.** Report SHA, test results and role counts. No paid provider/keys/order. |

**New-chat handover:** Guideline 0/0A/B/E/F → this checkpoint → M04 checklist + evidence. Accepted M03 source tests not rerun without functional change. Nothing in M04-A grants LIVE or claims real AI inference.

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
**Status: M03-A through M03-I accepted for scoped PUBLIC observation/closed-candle cohort (M03-I 8/8 VPS PASS + aligned subset8/20). G1 trusted issuer and complete trade history still absent. M04-A next for safe source-bound three-role wiring; no live/provider GO.**
- [x] **M03-A ACCEPTED (VPS `bdcd9f5`):** 9/9 scoped tests PASS; full IDR discovery 398 pairs, 312 eligible, shortlist30, max2 diagnostic watchlist; Final REJECT.
- [x] **M03-B ACCEPTED (VPS `9bf4e3a`):** after targeted continuity fix, 7/7 scoped tests PASS; public deep inspection BTC/IDR and ETH/IDR exit0, score75 vs score30, open-book/visible-flow evidence, Final REJECT and source untrusted.
- [x] **MAIN asset isolation CODED & VPS tested in M03-B:** PEPE/FLOKI excluded MAIN (reserve/rebuy buckets), USDT/USDC excluded settlement stablecoins. Dynamic capital ownership still M06, not proven.
- [x] **M03-C CODED:** persistent SQLite scan cursor + per-trace idempotent atomic reservation, restart-safe NEXT batch, 12s minimum start spacing, max 2 deep pairs per cycle; no new scanner/ledger.
- [x] **M03-C WIRED:** `--rolling-deep-once` → one orchestrator → existing DailyPairScanner → journal batch reserve → existing deep_scan offset → per-batch diagnostics → persisted REJECT.
- [x] **M03-C TESTS WRITTEN:** 7 tests in `tests/test_m03c_rotation.py` for cursor wrap, restart, replay/different-universe conflicts, cooldown, race, validation, mainpath and CLI no-LIVE.
- [x] **M03-C SCOPED VPS PASS:** 7/7 tests on VPS SHA `9e2744b`, 0.322s; SQLite restart/replay, cooldown, concurrent reservation and no-trade integration.
- [x] **M03-C REAL PUBLIC PROOF:** one `--rolling-deep-once` VPS scan (398 IDR, shortlist30, BTC+ETH, cursor0→2, exit0); BTC score70, ETH `NO_LAST_1M_VOLUME` rejected; reason `G1_ROTATING_PUBLIC_EVIDENCE_UNTRUSTED`, Final REJECT.
- [x] **M03-D CODED:** `deep_scan(require_btc=True)` on existing rolling path; if BTC not in batch observe BTC as bounded extra public source. BTC closed 15m trend + EMA8/21 determines diagnostic regime.
- [x] **M03-D CODED:** `_same_window_gate` permits diagnostics comparison only when selected pairs & BTC have valid 1m evidence, equal closed 1m epoch, matching closed 5m/15m starts, <=45s observed skew and <=60s freshness vs screening; otherwise fail-closed with zero ranked diagnostics.
- [x] **M03-D TESTS WRITTEN:** 8 cases in `tests/test_m03d_same_window.py` (includes 5m/15m context mismatch regression), plus affected M03-C assertion updated for new BTC context fetch. No test PASS claimed.
- [x] **M03-D VPS SCOPED PROOF ACCEPTED:** commit `5f14be87`, 15/15 tests PASS (0.351s), real public `--rolling-deep-once` 398 pair, shortlist30, rotated index2→4, FARTCOIN/IDR no last1m volume, BR/IDR score80/RSI100/volume82.89, `same_window.status=REJECT_CANDIDATE_EVIDENCE_INCOMPLETE`, empty ranked_same_window_diagnostics, Final REJECT. Explicit exit-code echo not included.
- [x] **M03-D/E same-window public accepted (affirmative diagnostic only):** M03-E VPS showed BTC+ETH closed1m epoch1793017500 with `ALIGNED_PUBLIC_UNATTESTED`, but still untrusted for trading.
- [x] **M03-E CODED:** provisional extreme-volume ratio >=10 paired with RSI >=90 or visible sell notional >=3x buy now flags `REJECT_MOMENTUM_ANOMALY`, suppressing score and any synchronized comparison. Not proof of manipulation and thresholds await replay.
- [x] **M03-E 7 TESTS AUTHORED:** `tests/test_m03e_anomaly.py` for BR-like spike, boundary, sell-dominance, benign high-volume, BTC/candidate same-window veto. **Not yet run.**
- [x] **M03-E accepted VPS:** commit `4cf220a7`, 29/29 tests PASS 0.367s; real public rolling BTC/ETH (closed 1m epoch 1793017500), `ALIGNED_PUBLIC_UNATTESTED`, BTC DOWNTREND, scores30/30, no anomaly flags this observation, Final REJECT. M03E_EXIT_CODE=0.
- [x] **M03-F CODED:** one `_quality_review` in existing `deep_analysis.py` returns caution flags using BTC downtrend, low volume, RSI extremes, book sell dominance and **visible sampled** trade pressure. Same-minute provisional watchlist <=2, explicitly NOT trade signal and G2 authorization always false.
- [x] **M03-F TESTS AUTHORED:** `tests/test_m03f_quality.py` has 7 new regression cases for bearish/sell-book/RSI/volume caution, aligned public preview and empty preview on misalignment. NOT YET RUN.
- [x] **M03-F VPS ACCEPTED:** SHA `40771ea7`, 29/29 scoped tests PASS 0.314s, real rolling BTC/ETH aligned epoch1793020860, BTC DOWNTREND, quality reviews for RSI/volume/book/visible sells, **provisional preview EMPTY**, Final REJECT and `M03F_EXIT_CODE=0`. No G1 trust.
- [x] **M03-G CODED:** `PublicScanContext` shares pairs/server/ticker_all across 20–40 diagnostic observations. Existing `observe` still default compatible; context mode fetches 4 per pair vs 7. Local budget estimate 3+4*N plus BTC context if missing; max 168 requests leaving 12 beneath published 180. **Not a cross-process limiter**.
- [x] **M03-G WIRED CODE:** same orchestrator `--bulk-deep-once` performs scanner→shared public 20-pair deep observations→30-second hard abort/complete coverage+same-minute BTC guard→SQLite Final REJECT; no AI or execution.
- [x] **M03-G 9 TESTS AUTHORED:** `tests/test_m03g_bulk.py`, covering shared GETs, expired context, 20/40 budget calculations, one invalid pair/no false ranking, expiry, bad limits and CLI REJECT. **No tests run yet.**
- [x] **M03-G VPS SCOPED TESTS:** SHA `567eba1a`, M02 11/11, M03-A 9/9, M03-G 9/9, 29/29 PASS.
- [x] **M03-G PUBLIC 20-PAIR PROBE (PARTIAL SOURCE) ACCEPTED:** all 20 attempted and analyzed, 12 scored, 8 rejected, 83 estimated GET, no time expiry, same-window guard rejected, output exit0 / Final REJECT. **Not** verified as 20 synchronized valid candidates.
- [x] **M03-H CODED:** existing public-market per-client attempted `_get` counter and `bulk_public_deep_scan` rejected-pair detail/causes, reason counts, coverage state; no additional API endpoints or requests.
- [x] **M03-H CODED & WIRED:** existing CLI reports `PUBLIC_BULK_INCOMPLETE_UNATTESTED` for incomplete source evidence; journal and Final remain REJECT, execution OFF.
- [x] **M03-H TESTS AUTHORED:** six tests in `tests/test_m03h_coverage.py` for 8 rejects, source error, deadline, GET count, aligned-but-untrusted and CLI incomplete label. Not run yet.
- [x] **M03-H VPS PROOF ACCEPTED:** SHA `6d413428`, scoped tests M02 11/11, M03-G 9/9, M03-H 6/6 PASS (26/26), real 20/20 bulk, 8 valid and 12 rejected (9 NO_LAST_1M_VOLUME, 2 SCALPING_SPREAD_TOO_WIDE, 1 EXTREME_PUBLIC_MOMENTUM_REVIEW_REQUIRED), 83 actual public GET, no expiry, Final REJECT, exit0.
- [x] **M03-I CODED:** existing bulk analyzer now carries an `eligible_cohort` subset with strict fresh source/BTC/1m+5m+15m alignment checks; complete 20-pair `same_window` remains FAIL if any selected pair is invalid. Separate cohort cannot authorize G1/G2/BUY.
- [x] **M03-I TESTS WRITTEN:** 8 cases in `tests/test_m03i_valid_cohort.py` to validate 12/8 or 19/1 subsets, failed BTC, mismatched 1m, expiry, zero-valid, full-public still no trade, CLI safety. Not yet run on VPS.
- [x] **M03-I FIRST VPS DIAGNOSTIC:** SHA `19e1313`: 9/9 M03-G and 6/6 M03-H PASS, M03-I 7/8 OK + 1 ERROR from invalid **test fixture**: shifted `closed_1m_epoch + 60` ahead of observed exchange time. `set -eu` stopped before public run, `M03I_EXIT_CODE=1`.
- [x] **M03-I FIX CODED:** isolated edit to `tests/test_m03i_valid_cohort.py`, shift 1m `start` and `closed_1m_epoch` **-60** instead, preserving SourceReceipt contract. No production logic loosened.
- [x] **M03-I VPS ACCEPTED:** SHA `c3fe498c`, tests 8/8 PASS 0.062s; real 20 pair/83 public GET, 8 candidate scores, 12 rejected solely `NO_LAST_1M_VOLUME`; eligible subset same-minute `ALIGNED_PUBLIC_UNATTESTED` epoch1793023680; complete-population `same_window=REJECT_INCOMPLETE_OR_EXPIRED_BULK_COVERAGE`, BTC DOWNTREND, provisional preview empty, Final REJECT, exit0.
- [ ] **M03-C FULL COVERAGE DESIGN:** 20–40 pair deep evidence collected with actual cost/rate budget and **same-valid-window source provenance**; individual batches must NOT be merged as live rank if stale.
- [ ] BTC regime context, listing/suspend/min-notional filters, 1/5/15m temporal join, trade-flow completeness, manipulation/surge-pullback/reversal/false-breakout and historic ablation remain unfinished.
- [ ] Create trusted G1 source issuer with immutable source provenance, G2-ready 1–2 candidates only when complete fresh evidence is proven. RISK and FINAL remain closed until later milestones.
**Acceptance:** M03 overall is PARTIAL; M03-B score alone is not a trade signal or projected net ROI. NO BUY / LIVE OFF.

### M04 — 3 AI MARKET → RISK → STRATEGY (ACTIVE)
**Status: M04-A CODED, not tested on VPS; 3 live/paid AI providers still NOT_CONNECTED.**
- [x] **M04-A CODED:** One source-bound shadow contract in `three_role_ai.py`. Order exactly MARKET → RISK → STRATEGY; RISK NO_TRADE/EXIT_REVIEW vetchains Strategy and cannot be bypassed.
- [x] **M04-A CODED:** Stable SHA256 binds local trace/pair/closed epoch, BTC regime and source flags. **This digest does NOT authenticate the public source or establish G1 provenance.**
- [x] **M04-A CODED:** Fail-closed gate requires compatible source schema, a current closed candle, candidate in same-window preview with zero quality cautions, G1 verified issuer AND complete trade coverage at cohort/root, before even considering model calls. Production public bulk cannot satisfy these gates.
- [x] **M04-A MAINPATH WIRED:** existing `--bulk-deep-once` → orchestrator source bound `ThreeRoleShadow` → CLI `ai_shadow` → existing persisted Final REJECT. No new scanner, no paid HTTP AI, no exchange executor. Caller injected for **unit tests only**, not a configured production provider.
- [x] **M04-A 10 CASES AUTHORED:** `tests/test_m04a_three_role_shadow.py` covers real public untrusted blocks, no-preview/cautions, synthetic test-only Market/Risk/Strategy sequence, Risk veto, output hash mismatch, invalid risk/negative ROI, stale source, CLI safety. **Not yet run VPS.**
- [ ] **M04-A VPS PASS:** test new M04-A contract + affected bulk M03-I and run real public `--bulk-deep-once`, confirm all public/model/execution statuses remain safe, count of actual AI provider calls zero.
- [ ] **M04-B PROVIDER:** configurable Sumopod adapter + availability/pricing proof, per-role model IDs, provider real structured responses, timeout/cost accounting; must never call paid provider on untrusted or stale G1 evidence. Needs deliberate credential approval/config.
- [ ] **M04-C QUALIFIED G1:** source issuer and evidence coverage proven, shared pair/candle snapshot to 3 roles, no spoofable trust flag as authorization, safe retry/dedupe/source expiry and historical replay.
- [ ] MARKET evidence-based BTC/trend/volume/flow assessment from a source-bound snapshot.
- [ ] RISK veto over downside/liquidity/slippage/listing/balance, no override.
- [ ] STRATEGY safe sizing and fee/slippage-adjusted net entry/exit; final policy remains separate and two outputs PASS/REJECT only.
**Acceptance:** actual provider+market evidence and valid Market/Risk/Strategy chain proven independently; synthetic injected responses or a SHA digest never certify real AI/provider/BUY.

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
| M03-B ERROR (kept for provenance) | `44ec7c43`, patch `22f8acad` | 33 tests initially, 5 NameError; corrected candle continuity with a/b. | This incident CLOSED by following 7/7 PASS run; not a current blocker |
| M03-B VPS ACCEPTED | `9bf4e3a` owner output | **7/7 PASS, public deep BTC/IDR score75 ETH/IDR score30, exit0**, two 1m/5m/15m source observations; REJECT, G1 untrusted, `trade_history_complete=false` | Complete trade source coverage/G1 issuer/risk/AI; no live order |
| M03-C VPS ACCEPTED | `9e2744b` user VPS 26 Oct 2026 | 7/7 tests PASS; real public rolling BTC+ETH, 398 IDR pairs, next_index2, BTC score70, ETH safely rejected no volume; exit0, FINAL REJECT, no order | Same-window BTC guard now CODED in M03-D but not verified; full coverage/G1 issuer pending |
| M03-D VPS ACCEPTED (rejection branch) | `5f14be87` supplied VPS output 26 Oct 2026 | 15/15 tests PASS, public rolling with FARTCOIN missing last-1m volume and BR score80/RSI100/82.89x volume. `same_window=REJECT_CANDIDATE_EVIDENCE_INCOMPLETE`, empty ranking, Final REJECT | Aligned positive public gate still not observed. Echoed M03D_EXIT_CODE not provided. No G1 authorization |
| M03-E VPS ACCEPTED | `4cf220a7`, owner supplied 26 Oct 2026 | 29/29 scoped tests PASS (0.367s), BTC+ETH same-window aligned public on epoch 1793017500, BTC downtrend, both diagnostic30, no anomaly flags, Final REJECT, exit0 | Untrusted coverage, all-30 deep same-window, replay/AI/order not proven |
| M03-F VPS ACCEPTED | `40771ea7` owner VPS 26 Oct 2026 | 29/29 scoped tests PASS, public BTC+ETH aligned epoch1793020860, quality caution flags, preview [], Final REJECT, exit0. | Not a trusted G1/qualified AI/trading source; heuristic thresholds not backtested |
| M03-G PUBLIC VPS PASS / PARTIAL EVIDENCE | `567eba1a` user VPS 26 Oct 2026 | M02 11/11, M03-A 9/9 and M03-G 9/9 PASS. 20 attempted, 12 valid, 8 rejected; 83 **estimated** GET, no expiry, no combined ranking, Final REJECT, exit0. | Eight reasons not surfaced; no 20/20 aligned accepted, no order. |
| M03-H VPS ACCEPTED | `6d413428` owner VPS output | Scoped 26/26 PASS, real 20-pair public bulk: 8 scored/12 rejected, 83 actual attempted GET, no expiry, reasons nine missing 1m volume/two wide spread/one momentum anomaly, Final REJECT, exit0 | Full 20/20 synchronized evidence/G1 trust not achieved; causes are one-time sample only. |
| M03-I VPS ACCEPTED (source-cohort only) | `c3fe498c` user supplied VPS 26 Oct 2026 | 8/8 tests PASS; 20 public pair analyzed, 8 valid and 12 missing last1m volume, 83 public GET; eligible same-minute cohort 8 on closed epoch1793023680, BTC downtrend, preview empty; Final REJECT exit0 | Still source-untrusted, G1 issuer/trade tape incomplete, no executable proposal or AI. |
| M04-A CODED ONLY | `three_role_ai.py`, `deep_analysis.py`, `orchestrator.py`, `__main__.py`, `tests/test_m04a_three_role_shadow.py` | One source-bound 3-role Market→Risk→Strategy shadow boundary is wired into existing bulk mainpath; source, Risk veto & typed output checks implemented; **10 tests authored, no VPS PASS yet** | Untrusted G1 blocks all real provider calls; Sumopod model adapter and actual 3-role inference still not connected; no BUY |
| M04-B–M12 | Belum ada acceptance | Tidak boleh dicentang atau dilabel selesai | Provider and G1 trust, Telegram, capital, preflight, execution and SELL |

**Catatan:** Tanggal baseline tidak berarti semua isi tersertifikasi hari itu. Ketika bukti baru diverifikasi, tambahkan **satu baris** sesuai milestone. Jika hanya perubahan status, jangan memodifikasi aturan trading di guideline.

## 5. FORMAT WAJIB SETELAH SETIAP TAHAP

Tulis ringkas di akhir sesi, dan perbarui checklist + checkpoint di sini:

1. **DIBUAT:** path kode/fitur aktual dan commit SHA.
2. **TERSAMBUNG:** caller → callee → bukti jalur utama; tulis jelas jika belum.
3. **DIPERIKSA:** tes per fungsi berubah, PASS/FAIL teramati, sumber real/mock/private, reference.
4. **BELUM:** error, blocker, izin atau tahapan yang belum tercapai; jangan menulis DONE bila tidak benar.
5. **NEXT SATU LANGKAH:** satu vertical slice yang paling mendekatkan pada bot scalping harian terintegrasi.

**Syarat selesai proyek akhir:** source scanner real, top candidates, MARKET/RISK/STRATEGY, PASS/REJECT MANUAL/AUTO, authenticated capital safe, idempotent BUY/SELL, protective exit, order/fill ownership, settled IDR dan realized net PnL; semua terbukti terhubung pada satu runtime dan ada persetujuan owner untuk LIVE. Tak ada jaminan Rp100.000 laba harian.
