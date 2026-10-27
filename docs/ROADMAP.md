# ROADMAP & EXECUTION CHECKLIST — INDODAX AI BOT V2

**Pemilik:** dvoriano/indodax-ai-bot-v2, branch main  
**Tanggal baseline:** 26 Oktober 2026 (WIB)  
**ATURAN YANG MENGIKAT:** [PROJECT_GUIDELINE.md](PROJECT_GUIDELINE.md) terutama 0, 0A, B, C, D, E, F.  
**Fungsi file ini:** satu-satunya **tracker checklist dan handover**. Guideline mengatur spesifikasi/keputusan; roadmap mencatat pekerjaan/bukti. Jika tidak sejalan, guideline menang. Jangan membuat roadmap/checkpoint/handover kedua.

> **MISI BOT = DAILY SPOT SCALPING**, pemantauan 24/7, keputusan berdasarkan candle 1m closed dengan konteks 5m/15m dan BTC, akhir MAIN di IDR. Tidak boleh memaksa transaksi untuk mencapai target Rp100.000 **NETTO REALIZED per hari WIB**. Tidak boleh mengubah kebijakan dari guideline tanpa keputusan owner.

## 1. START HERE: HANDOVER SINGKAT SETIAP PINDAH CHAT

| Bidang | Checkpoint terkonfirmasi / status kerja |
| --- | --- |
| **ONE ACTIVE BLOCKER** | **M04-O QUALITY VERIFIED; M04-P PUBLIC WS GAP-SEGMENT RECOVERY CODED, VPS PROOF PENDING.** Owner last attempted PUBLIC restart failed on first session with `WS_SESSION_REJECTED:WS_STREAM_GAP_OR_EPOCH_CHANGED` and stayed `failed`, exit3. Local journal remained epoch1633996541, offset14644819, 1259 publications. Existing supervisor changed-scope 12/12 tests PASS VPS. No evidence of data loss being filled. Owner VPS SHA `babc6274`: six new M04-O tests **6/6 PASS** in 0.110s; one REAL `--bulk-deep-once --ws-db runtime/public_ws_btc.sqlite3` produced `PUBLIC_BULK_INCOMPLETE_UNATTESTED`, BTC REST closed minute START `1793084820`, WS sample `PUBLIC_SAMPLE_EMPTY` with **0 observed WS trade rows**, 1 REST-visible trade, `trade_row_delta_ws_minus_rest=-1`, `buy_idr_delta_ws_minus_rest=-255728.55096000`, sell delta 0; `PUBLIC_VISIBLE_SAMPLES_DIFFER_UNATTESTED`; G1 verified false, completed AI roles [], Final REJECT, execution false, command `M04O_EXIT_CODE=0`. **Crucial: user systemd `ActiveState=failed`, `SubState=failed`, `NRestarts=24`.** The exit0 of the batch probe DOES NOT mean collector healthy because service check was only printed, not asserted. |
| **M04-O acceptance boundary** | Same-G1 mainpath correctly compares two **nonexhaustive** public BTC 1m samples (REST vs historical persisted WS). The observed WS=0 alongside one REST trade may reflect stopped/disconnected collector, unobserved public events or partial source coverage. It does NOT diagnose exactly when/why the service failed, or prove the exchange had only one trade. No change to authorization. M04-N earlier live join 13/13 and M04-M repeated-public-collection evidence remain accepted historic snapshots, NOT a current-running guarantee. |
| **FAILURE MECHANISM FROM REPO** | Existing `deploy/indodax-v2-public-ws-btc.service` explicitly uses `Restart=on-success` and stops after nonzero batch result (fail closed). `public_ws_supervisor.py` marks incomplete/rejected session `status=REJECT` but returns a generic `PUBLIC_WS_BATCH_INTERRUPTED_OR_UNVERIFIED` and may not expose internal cause from lower collector in report. Hence `failed` is compatible with safe intentional stop; not yet evidence of corrupted DB or permanent exchange outage. Do NOT change restart policy to `always`, auto-reset fail state, purge SQLite, reinitialize cursor, or authorize trading. |
| **Read-only VPS diagnosis** | systemd `Result=exit-code`, `ExecMainStatus=3`, NRestarts24; last batch `REJECT` after 3/10, previously 10/10. SQLite epoch1633996541 unchanged, offset14644819 and 1259 publications, DISCONNECTED. Specific failed-session code not logged in original supervisor. Existing supervisor now CODED to include redacted `collector_failure_code`, `attempted_sessions` and `failed_session_number`; 12 changed-scope supervisor tests pending VPS. Recovery must be one guarded PUBLIC-only start after tests, never orders. |
| **ONE NEXT ACTION** | Pull latest V2 main; run **8 M04-P targeted tests**. Previous VPS run at SHA `fb9e07cf` was 4/7 PASS, 2 ERROR from missing SQL argument comma in `subscription()`, 1 FAIL downstream in collector mock (same bad call); due to `set -e`, M04-E/I/J and service start **DID NOT RUN**. SQL separator now repaired and one-rebaseline-per-batch cap tested. If 8 PASS, run affected M04-E 10, M04-I 7, M04-J 12 once; validate original installed unit unchanged, service remains `failed`, SQLite original epoch/offset/publications intact, then **one public-only start**, inspect new sidecar and status. No G1/AI tests or repeated start. |
| **M04-P CODED / NOT YET VPS TESTED** | Official Indodax supports offset replay but does not guarantee old history is still available. In existing `trade_stream.py`, incomplete replay now commits GAP instead of rolling back SQLite transaction; probe identifies persisted GAP and collector permits one bounded retry with a PUBLIC fresh baseline. Journal uses new append-only `ws_stream_discontinuities` table, retains all previous trade rows, refuses same-epoch cursor rewind, and reports `unrecoverable_gap_rebaseline_count` on G1 samples. No existing SQL tables removed; stream and G1 trade tape completeness remain FALSE, no G1 issuer or AI. New seven targeted tests authored, not yet VPS PASS. Source: official Indodax Marketdata-websocket documentation. |
| **M04-P latest VPS evidence** | At SHA `fb9e07cf`, `test_m04p_public_ws_segment_recovery.py` ran 7 cases: **4 PASS, 2 ERROR, 1 FAIL**. Source line 126 had `"SELECT ... pair=?" (pair,)` rather than SQL text + comma + tuple, causing `TypeError: 'str' object is not callable` in `subscription()`. Collector case failed because mocked second session calls that function and its generic exception path returns REJECT. **No E/I/J tests, SQLite guard, or systemd start executed**. Fix CODED at same stream function, plus explicit one baseline retry per batch. New M04-P test total **8**, pending VPS. |
| **SAFETY / SCOPE** | V2 only; no interaction with V1. PUBLIC BTC collection was sole 24/7 approved activity; currently FAILED so do not claim healthy continuous coverage. Paid Sumopod MARKET/RISK/STRATEGY, private Indodax API, Telegram order final, real BUY/SELL remain OFF. `INDODAX_TRADING_ENABLED=NO`, `INDODAX_ALLOW_LIVE_EXECUTION=NO`, `INDODAX_EXECUTION_MODE=DRY_RUN`; G1 issuer not qualified and Final REJECT. |

**New-chat handover:** Only guideline + this ROADMAP. M04-O accepted 6/6+real public REST/WS differential, but the PUBLIC BTC user systemd collector FAILED after NRestarts24. Next run one nonmutating VPS evidence command and resolve failure, not another scanner, status file, or routine regression pass.

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
**Status: M04-J VERIFIED_PUBLIC_ONLINE for finite PUBLIC supervised batch on VPS `1dbe301d` (9/9 tests PASS, 2/2 real supervisor sessions, 0 persisted trade publications, Final REJECT exit0). Staged user systemd service not installed/enabled. M04-K read-only health visibility next. No verified Indodax trade tape, paid model, or orders.**
- [x] **M04-A CODED:** One source-bound shadow contract in `three_role_ai.py`. Order exactly MARKET → RISK → STRATEGY; RISK NO_TRADE/EXIT_REVIEW menghentikan Strategy and cannot be bypassed.
- [x] **M04-A CODED:** Stable SHA256 binds local trace/pair/closed epoch, BTC regime and source flags. **This digest does NOT authenticate the public source or establish G1 provenance.**
- [x] **M04-A CODED:** Fail-closed gate requires compatible source schema, a current closed candle, candidate in same-window preview with zero quality cautions, G1 verified issuer AND complete trade coverage at cohort/root, before even considering model calls. Production public bulk cannot satisfy these gates.
- [x] **M04-A MAINPATH WIRED:** existing `--bulk-deep-once` → orchestrator source bound `ThreeRoleShadow` → CLI `ai_shadow` → existing persisted Final REJECT. No new scanner, no paid HTTP AI, no exchange executor. Caller injected for **unit tests only**, not a configured production provider.
- [x] **M04-A 10 CASES ACCEPTED:** `tests/test_m04a_three_role_shadow.py` covers real public untrusted blocks, no-preview/cautions, synthetic test-only Market/Risk/Strategy sequence, Risk veto, output hash mismatch, invalid risk/negative ROI, stale source, CLI safety. **10/10 PASS on VPS SHA `232da715`**.
- [x] **M04-A VPS ACCEPTED:** SHA `232da715`, 8/8 M03-I + 10/10 M04-A PASS; real 20 pair, 10 diagnostic-valid/10 rejected, 83 public GET; BTC_UPTREND, aligned cohort10, warning on all, preview [], `ai_shadow.completed_roles=[]`, `reason=NO_PROVISIONAL_CANDIDATE`, `provider_connected=false`, Final REJECT, exit0. Public G1 does not authorize any provider or order.
- [x] **M04-B CODED:** one `sumopod_provider.py` for three configurable MARKET/RISK/STRATEGY model IDs via `SUMOPOD_MODEL_*`, documented official fixed HTTPS chat completions URL, secret-safe JSON parsing, response limit, timeout, usage token count, and explicit paid-network OFF default.
- [x] **M04-B SAME MAINPATH WIRED FOR TEST INJECTION:** orchestrator receives optional `ai_role_caller` dependency but production CLI supplies None. `ThreeRoleShadow` binds candidate candle/RSI/spread/volume/order-book diagnostic into local SHA and passes validated prior MARKET/RISK results downstream. This does NOT establish G1 provenance.
- [x] **M04-B 11 TESTS ACCEPTED:** `tests/test_m04b_sumopod_adapter.py` mocked 3 role IDs, JSON/digest, safe errors, usage, paid-network OFF and no-call wiring. **11/11 PASS VPS `553232b4`; NOT real paid inference.**
- [x] **M04-B VPS ACCEPTED for MOCK ADAPTER / PUBLIC no-call:** SHA `553232b4`, M04-A 10/10 + M04-B 11/11 PASS. Public 20/20, 10 valid/10 rejected, 83 GET; 7 NO_LAST_1M_VOLUME, 2 SCALPING_SPREAD_TOO_WIDE, 1 EXTREME_PUBLIC_MOMENTUM_REVIEW_REQUIRED; BTC UPTREND, candidate warnings present, preview empty, `ai_shadow.completed_roles=[]`, provider_connected=false, Final REJECT, exit0. No paid real model.
- [ ] **M04-B ACTUAL PROVIDER PROOF:** validate three available Sumopod model IDs/prices separately, owner-authorized key/budget, trusted source receipt, 3 real role outputs and token cost. **BLOCKED** until source+release controls; do not request keys or spend now.
- [x] **M04-C CODED:** `g1_receipt.py` independently verifies INTERNAL HMAC issuer receipt over trace, pair, close epoch, local source digest and TTL, with caller-supplied key only. This does NOT authenticate Indodax.
- [x] **M04-C WIRED:** existing `ThreeRoleShadow` requires independently injected receipt verifier before model caller; public G1 trust flags or local SHA alone cannot authorize role calls. Production CLI supplies no verifier, no secret and no model.
- [x] **M04-C 10 TESTS AUTHORED:** `tests/test_m04c_g1_receipt.py`, plus modified M04-A/B synthetic receipt tests. Forged booleans, tampered market facts, expiry, replay, wrong key and MAC cannot reach AI. **No VPS PASS yet.**
- [x] **M04-C FIRST VPS EVIDENCE:** at SHA `2db2c393`, M04-A 9/10 PASS, 1 FAIL in invalid-Risk test using unsigned `source(trusted=True)`. New independent receipt guard correctly rejected with `G1_ISSUER_RECEIPT_UNVERIFIED`, before reaching Risk; exit1 prevented M04-B/C and public probe.
- [x] **M04-C TARGETED TEST FIX CODED:** only `tests/test_m04a_three_role_shadow.py` changed to use test-only signed fixture; commit `84693a53`, no production auth/AI/trading changes.
- [x] **M04-C VPS RETEST ACCEPTED (scoped):** user VPS SHA `84112ca5` fixed M04-A 1/1 PASS, M04-B 11/11 PASS, M04-C 10/10 PASS; public 20 pair, 7 scored/13 rejected, 83 GET; BTC anomalous and no AI provider roles, Final REJECT. Explicit final echo truncated.
- [x] **M04-D CODED:** per-pair source coverage from real received public observations, visible recent-trade count versus proven coverage, missing/expired and forged source-claims classified but NEVER promoted. New `source_coverage.py` called inside existing 20-pair bulk, no extra GET.
- [x] **M04-D 8 TESTS AUTHORED:** `tests/test_m04d_source_coverage.py` covers 20-pair sample, missing rows, timeout, forged completion flag, aligned-only cohort and existing mainpath Final REJECT. Not run on VPS.
- [x] **M04-D VPS ACCEPTED:** SHA `cf68eb4e`, 8/8 source coverage tests PASS 0.080s, live public 20/20, 8 diagnostic-valid/12 rejected (11 missing last 1m volume, 1 wide spread), 83 measured GET, all 20 pairs 500 recent REST trade rows but 0–7 in closed-minute window. Source admission `REJECT_SOURCE_NOT_QUALIFIED`, no G1 issuer, roles=[], Final REJECT, exit0.
- [x] **M04-E CODED:** new `trade_stream.py` receiver for official public trade-activity WS with SQLite cursor/epoch, strict 7-field trade parser, frame/sequence validation, recovery from documented offset, gap/replay handling and read-only 12sec WS probe. Existing orchestrator now offers `run_public_ws_cycle`; CLI `--public-ws-once`. No private key or order; optional `websocket-client` import at runtime.
- [x] **M04-E 10 TESTS ACCEPTED:** `tests/test_m04e_trade_stream.py` passed 10/10 VPS on SHA `14d64a15`, covering synthetic recovery/gap/epoch, missing package and CLI safe REJECT. **Not** real WS trade-delivery proof.
- [x] **M04-E VPS SESSION ACCEPTED (baseline only):** SHA `14d64a15` 10/10 tests PASS, real BTC/IDR WS auth+subscribe+12.09sec session `M04E_EXIT_CODE=0`, epoch1633996541, offset14642052, **0 publications**, BASELINE_ONLY, Final REJECT. No real transaction delivery/restart recovery established.
- [x] **M04-F CODED:** official recovery examples can include the requested resume offset, so accept inclusive replay boundary without counting it as a new publication; reject mismatching persisted digest. Block old socket frames until fresh subscription ACK. Maintain GAP/epoch fail-closed.
- [x] **M04-F CODED:** official WS method 7 heartbeat + explicit diagnostic when trade channel silent; record postclose DISCONNECTED and in-probe state separately. Same V2 orchestrator public-only CLI.
- [x] **M04-F 7 TESTS FIRST VPS:** SHA `1b47e7b2`, 6/7 PASS. `test_quiet_channel_heartbeat_ack_proves_protocol_alive_not_trade_tape` fails because successful ACK was overwritten to `False` solely to prevent duplicate disconnect; test exposed an actual reporting bug.
- [x] **M04-F FIX CODED:** separate `disconnect_recorded` cleanup marker from `acked`; retain truthful `subscription_acknowledged=true`, do not duplicate SQLite disconnect write; production WS validation/offset/provenance/trade guards unchanged. Commit pending scoped VPS test.
- [x] **M04-F VPS RETEST ACCEPTED:** owner VPS SHA `513dc284` fixed ACK test 1/1 PASS. Two real BTC-IDR 30s PUBLIC WS sessions same DB: session1 2 live publications, persisted2 offset14642460; session2 1 live publication, persisted4 offset14642462, same epoch; both heartbeat+ACK confirmed, stream during probe `CONTIGUOUS_OBSERVED`, persisted after probe `DISCONNECTED`. Final REJECT, `M04F_FIX_EXIT_CODE=0`. Difference consistent with uncounted recovered publication but not independently attributed yet.
- [x] **M04-G CODED:** measure actual server WS subscription replay-frame count, number of newly persisted recovered publications, number of new live-frame publications, resume cursor/epoch and before/after SQLite counts in the existing PUBLIC mainpath, no source-trust promotion.
- [x] **M04-G 6 TESTS AUTHORED:** `tests/test_m04g_recovery_accounting.py`, synthetic initial session, inclusive replay, no-new resume, boundary-only, unresolved gaps and nonconsecutive replay. **Not yet run VPS.**
- [x] **M04-G VPS ACCEPTED:** SHA `037ce7e4`, 6/6 PASS 0.339s; real WS reconnect 27 replayed and 27 new SQLite rows, 0 live, total4→31, offset14642462→14642489, epoch1633996541 stable, heartbeat+ACK true, `REPLAY_NEW_PUBLICATIONS_PERSISTED`, Final REJECT, exit0. Only PUBLIC observed/replayed coverage.
- [x] **M04-H CODED:** `PublicTradeStreamJournal.closed_minute_sample` aggregates one closed-minute diagnostic from existing WS SQLite and guards digest/duplicate/row count; `run_bulk_deep_cycle` optionally joins via CLI `--ws-db`, never upgrades source, role or Final authorization.
- [x] **M04-H 8 TESTS AUTHORED:** `tests/test_m04h_closed_minute_join.py` checks per-side minute totals, open/empty minute, tampered rows, duplicate trade identity, bounded scan and actual CLI join/missing file. **Not tested VPS.**
- [x] **M04-H VPS SCOPED ACCEPTED:** VPS SHA `310944c7`, 8/8 tests PASS (0.463s). Real `--bulk-deep-once --ws-db` reports `NO_ALIGNED_BTC_CLOSED_MINUTE`, `g1_verified=false`, `ai_shadow.completed_roles=[]`, Final REJECT, execution OFF. Real matched-minute join not yet proven; exit echo not supplied.
- [x] **M04-I CODED, SAME MAINPATH:** `collect_official_public_ws` reuses SQLite journal and verified WebSocket probe across bounded short sessions. Same V2 orchestrator exposes `--public-ws-collect`; rejects heartbeat failure, GAP, malformed role authority and API errors, never grants trading.
- [x] **M04-I 7 TESTS AUTHORED:** `tests/test_m04i_ws_collector.py` tests two-session recovery, missing heartbeat, gap stop, mid-collection probe failure, invalid bounds, CLI fail-closed and nonzero interrupted exit. Tests NOT YET run VPS.
- [x] **M04-I VPS ACCEPTED (bounded PUBLIC only):** SHA `8be8fbfb`, 7/7 PASS 0.238s; real 3×20s BTC WS sessions via shared SQLite with no session faults, heartbeat+ACK true, total 129 recovered + 3 live, 163 persisted, offset14642489→14642621, Final REJECT, `M04I_EXIT_CODE=0`. No full historical tape proof or always-on systemd.
- [x] **M04-J CODED:** `public_ws_supervisor.py` orchestrates one lock-protected finite public WS collection batch using the existing collector and SQLite, fail-stop and atomic last-batch status file; same single V2 orchestrator and CLI `--public-ws-supervised-once`. No issuer/AI/trade path.
- [x] **M04-J STAGED OPS UNIT:** `deploy/indodax-v2-public-ws-btc.service` is a NON-INSTALLED user systemd template, restarts **only** after clean batch exit0, stops on failures, uses separate V2 runtime DB, no secrets and all trading flags OFF. No VPS unit installed/enabled or tested.
- [x] **M04-J 9 TESTS AUTHORED:** `tests/test_m04j_ws_supervisor.py` checks atomic status+mode, failure, redaction, forged authorization flag, process lock, invalid input, CLI Final REJECT/exit3, and static staged unit safety. Tests have NOT RUN on VPS.
- [x] **M04-J VPS ACCEPTED, bounded supervisor only:** SHA `1dbe301d`, nine tests PASS (0.200s); real PUBLIC pilot two supervised sessions, status `PUBLIC_COLLECTION_BATCH_OK_UNATTESTED`, offset14642933, 0 persisted publications in fresh pilot DB, Final REJECT, `M04J_EXIT_CODE=0`. `restart_only_after_clean_batch=true`; `systemd_service_active=null`, NOT a running 24/7 service.
- [x] **M04-K CODED:** `public_ws_health.py` read-only status+SQLite age/epoch/offset/count validation; no DB/journal creation, no source/AI/execution authorization, active systemd UNKNOWN.
- [x] **M04-K CLI WIRED:** `--public-ws-health --db ... --supervisor-status-file ... --health-max-age-seconds 3600` returns exit0 only for recent consistent LAST BATCH; otherwise status REJECT/exit3. No model order or account APIs.
- [x] **M04-K STATUS EPOCH CODED:** supervisor now writes `stream_epoch` for newly completed batches. Existing old checkpoint lacks epoch and intentionally cannot pass strict reader. Existing M04-J safety decision unchanged.
- [x] **M04-K 10 TESTS AUTHORED:** `tests/test_m04k_ws_health.py` covers age/future, forged status, DB rollback/epoch change, missing file/no side effects, corrupted/oversized JSON, invalid bounds, CLI read-only and stale nonzero. NOT YET VPS-run.
- [x] **M04-K VPS ACCEPTED (last completed PUBLIC batch only):** SHA `7b5e91cb`, 10/10 new tests PASS in 0.380s, real 2/2 supervised public BTC WS sessions, 1 publication, epoch1633996541, offset14643217, health `RECENT_COMPLETED_PUBLIC_BATCH_UNATTESTED`, batch_age_seconds=0, Final REJECT, `M04K_EXIT_CODE=0`. Neither systemd nor live stream connectivity proven.
- [x] **M04-L CODED:** `public_ws_unit_status.py` and `--public-ws-unit-status` read staged/installed user systemd unit matching approved V2 template, and inspect loaded/active/enabled/PID via read-only systemctl show. Distinguish STAGED_NOT_INSTALLED vs RUNNING, reject differing/symlinked/unsafe units; never issue systemctl install/start/enable or trading permission.
- [x] **M04-L 11 TESTS AUTHORED:** `tests/test_m04l_ws_systemd_unit.py` covers safe uninstalled template, inactive and active user unit, changed unit/symlink/unsafe staging, unavailable user manager, wrong fragment, CLI read-only and reject nonzero. NOT YET VPS-tested.
- [x] **M04-L VPS ACCEPTED (staged readiness, NO activation):** owner VPS SHA `7a5c4006`, 11/11 targeted tests PASS in 0.011s. Real `--public-ws-unit-status` returned `STAGED_NOT_INSTALLED`, staged template safety valid, unit not installed/enabled/active, systemd running false, source and AI permissions false, Final REJECT, `M04L_EXIT_CODE=0`.
- [x] **M04-M OWNER GO SCOPED:** owner approved only continuous PUBLIC BTC/IDR WS collector via user systemd; private API, paid 3-AI and LIVE trade remain expressly unauthorized.
- [x] **M04-M EARLY VPS ENVIRONMENT BLOCKER FOUND:** owner V2 SHA `2ded9c2c`, pre-install `/usr/bin/python3` has no `websocket` module; `M04M_EXIT_CODE=1`. No activation performed.
- [x] **M04-M V2 PYTHON FIX CODED:** staged user systemd unit switched from `/usr/bin/python3` to `%h/indodax-ai-bot-v2/.venv/bin/python`. Added hash-pinned `deploy/requirements-public-ws.txt` for `websocket-client==1.9.2` and adjusted exact-ExecStart guard in `public_ws_unit_status.py`. Two focused unit tests added (11→13). ALL CODED/PENDING VPS validation, old successful 11 M04-L tests remain accepted unless affected by guard change.
- [x] **M04-M VPS SERVICE INSTALLED + RUNNING:** owner VPS SHA `6c7fd77b` 13/13 changed-scope user-unit tests PASS 0.012s; `websocket-client==1.9.2` installed in isolated V2 venv; `systemctl --user enable --now` succeeded, loaded/active/running/enabled and `MainPID=3012309`; `--public-ws-unit-status` reports exact staged-unit match and `USER_UNIT_RUNNING_OBSERVED`, Final REJECT, `M04M_EXIT_CODE=0`.
- [x] **M04-M REAL FIRST BATCH + ONE RESTART VERIFIED:** VPS real user systemd first 10/10 PUBLIC BTC sessions completed 2026-10-27 11:57:05 WIB; 14 SQLite publications, epoch1633996541, offset14643574, result `PUBLIC_COLLECTION_BATCH_OK_UNATTESTED`, Final REJECT. `NRestarts=1`, next process PID3013650 active, enabled, running. This proves initial automated restart and public WS collection, not sustained indefinite 24/7 uptime.
- [x] **M04-M FALSE-NEGATIVE HEALTH CONDITION FOUND IN CODE:** `public_ws_health` required CURRENT SQLite state DISCONNECTED when inspecting LAST batch sidecar; concurrently running next batch can set BASELINE_ONLY / CONTIGUOUS_OBSERVED. User observed health `WS_HEALTH_JOURNAL_MISMATCH_OR_ROLLBACK` age294; real SQLite current status not yet supplied. No claim genuine rollback.
- [x] **M04-M FIX CODED:** read-only consistent SQLite snapshot; accept safe known current states DISCONNECTED/BASELINE_ONLY/CONTIGUOUS_OBSERVED if saved last batch was DISCONNECTED and offset/count/epoch are nonregressing. Strictly REJECT GAP, RECOVERING, unknown state, rollback, changed epoch, stale batch; all G1/AI/trading authority unchanged.
- [x] **M04-M 5 NEW REGRESSION TESTS AUTHORED:** `tests/test_m04m_live_batch_health.py`; tests active new batch baseline and publication, GAP/RECOVERING rejected, count rollback rejected, health CLI remains read-only and Final REJECT. NOT VPS tested.
- [x] **M04-M LAST-BATCH HEALTH PATCH VPS ACCEPTED:** owner VPS SHA `ef2dccac`: 5/5 tests PASS (0.303s). Real persistent last batch 722 rows/offset14644282; current 742 rows/offset14644302 (+20), `BASELINE_ONLY` current vs `DISCONNECTED` last completed; epoch unchanged 1633996541. `--public-ws-health` now RECENT_COMPLETED_PUBLIC_BATCH_UNATTESTED, batch age309 seconds, Final REJECT, `M04M_HEALTH_FIX_EXIT_CODE=0`; user systemd active/running PID3025477 with NRestarts10. Not complete exchange tape or G1 auth.
- [x] **M04-N CODED, EXISTING G1 MAINPATH:** add observed BTC REST 1m diagnostic field in existing `bulk_public_deep_scan` even with rejected eligible cohort, no additional GET. Existing `run_bulk_deep_cycle` now joins actual observed BTC REST minute to existing V2 persistent WS SQLite `--ws-db`, rejects BTC cohort minute disagreement, no role/issuer/trade promotions.
- [x] **M04-N SQLITE CLOSED-MINUTE SCALING CODED:** `closed_minute_sample` now filters via JSON1 SQL for publications containing target minute trades, instead of applying 2000 limit to earliest epoch publications. One consistent DB snapshot, digest/duplicates/target-minute cap retained; full tape never inferred. Related M04-H bounded test updated to target-minute semantics.
- [x] **M04-N FIRST VPS CHECK 3/5 FAILED (REAL EVIDENCE):** owner VPS SHA `4c5946f3` ran M04-N 5 tests: 2 PASS and 3 FAIL in 0.353s. BTC REST diagnostic minute None caused fail of source, CLI join, conflict cases; `set -e` skipped affected M04-H test run and live PUBLIC bulk probe. No exact final M04N_EXIT_CODE supplied.
- [x] **M04-N CLOSED-MINUTE ROOT CAUSE FIX CODED:** receipt `SourceReceipt.closed_1m_epoch` is REST candle END (`latest_1.start+60`); WS sample requires START. Map only when actual OHLC.start+60==receipt.end, pass candle.start to WS; compare cohort END to start+60. Align H mainpath and N shared test fixtures to correct source/WS minute. No source-trust upgrade.
- [x] **M04-N SIX TARGETED TESTS READY:** `tests/test_m04n_live_btc_minute_join.py` now 6 cases incl matching end/start, H original 8 affected tests updated only for changed minute semantics. Not yet VPS retested.
- [x] **M04-N CLOSED MINUTE SEMANTIC VPS ACCEPTED:** owner VPS SHA `13f6f4ca` 6/6 M04-N tests PASS (0.435s). REST end / WS minute start reconciliation works in targeted source/CLI tests.
- [x] **M04-H JSON FILTER INTEGRITY FAILURE FOUND:** owner VPS same SHA 7/8 M04-H PASS, one digest-integrity case FAIL (0.436s); payload `rows_json='[]'` disappeared from JSON1 minute selection before original SHA256 could detect tampering. `M04N_FIX_EXIT_CODE=1`, so live bulk probe did NOT run.
- [x] **M04-N DIGEST GUARD FIX CODED:** journal now verifies bounded full epoch stored SHA256 (max 500000 pubs) via 512-row streaming chunks within a single read snapshot prior to exact-minute filtering. Tampered rows in or outside minute reject; large epochs fail closed, no memory-unbounded materialization.
- [x] **M04-N 3 NEW INTEGRITY TESTS CODED:** `tests/test_m04n_minute_integrity.py` asserts tampered timestamp hidden from minute search rejected, unrelated minute tampering rejected and valid minute reports digest verification without G1/AI/trade authority. Not yet VPS run.
- [x] **M04-N INTEGRITY + REAL PUBLIC G1 VPS ACCEPTED:** owner SHA `da796f4f`, **13/13** scoped tests PASS (3 M04-N integrity + 8 affected H + 2 targeted N). One real PUBLIC bulk mainpath `--ws-db runtime/public_ws_btc.sqlite3` returned `PUBLIC_BULK_INCOMPLETE_UNATTESTED`, BTC REST closed minute start 1793081040, `PUBLIC_SAMPLE_OBSERVED`: 5 visible trade rows, 4 WS publications, 1258 epoch SHA hashes verified. Source remains `FULL_MINUTE_TRADE_TAPE_NOT_PROVEN`, G1 false, no AI roles, Final REJECT, no execution. User systemd active/running NRestarts24. `M04N_INTEGRITY_EXIT_CODE=0`.
- [x] **M04-O PUBLIC QUALITY MAINPATH CODED:** existing `bulk_public_deep_scan` retains actual same-candle BTC REST visible trade count, buy/sell and total IDR without extra public HTTP calls. Existing `run_bulk_deep_cycle` reads already joined WS same-minute sample to build `btc_rest_ws_observability`: observed count/IDR deltas, matching vs differing unattested samples, explicit REJECT on missing, malformed or mismatched-minute input. No new scanner/provider; G1/AI/trading flags unchanged.
- [x] **M04-O 6 SCOPED TESTS WRITTEN:** `tests/test_m04o_btc_public_quality.py` tests equal and different visible samples, minute mismatch/forged trust, missing/invalid flows, REST reuse 20 requests, actual orchestrator CLI Final REJECT. **NOT YET VPS-tested.**
- [x] **M04-O VPS ACCEPTED FOR PUBLIC-ONLY QUALITY COMPARISON:** owner V2 SHA `babc6274`: six scoped tests 6/6 PASS in 0.110s, one real bulk probe over permanent BTC WS SQLite `PUBLIC_BULK_INCOMPLETE_UNATTESTED`, REST minute start1793084820, REST 1 visible trade, WS 0 visible, BUY difference -255728.55096000 IDR, `PUBLIC_VISIBLE_SAMPLES_DIFFER_UNATTESTED`, G1 false, AI roles [], Final REJECT, execution false, `M04O_EXIT_CODE=0`. This does not prove complete tape.
- [x] **M04-P ACTUAL FAILURE IDENTIFIED:** owner VPS at `1fdb708d` 12/12 updated supervisor tests PASS in 0.270s, cursor guard epoch1633996541/offset14644819/count1259 PASS, one PUBLIC-only manual restart exited3 on first attempt with `WS_SESSION_REJECTED:WS_STREAM_GAP_OR_EPOCH_CHANGED`, service remains failed. No unexpected orders, source remains untrusted.
- [x] **M04-P DISCONTINUOUS PUBLIC REBASELINE CODED:** bounded replay gap commits persistent GAP even with later replay frames. One controlled fresh-baseline retry when GAP is proven, without deleting prior rows or claiming continuity; append-only `ws_stream_discontinuities` records old/new epoch/cursor/time, prevents same-epoch rewind. Existing G1 minute observation exposes known gap count. Sidecar includes gap count and remains Final REJECT/trading OFF. Seven targeted tests authored, NOT VPS tested.
- [x] **M04-P FIRST VPS RUN EXPOSED TWO RELATED ERRORS:** 7 new tests at `fb9e07cf`: 4 PASS, 2 ERROR from missing comma in `subscription()` SQL parameters and 1 collector FAIL downstream, aborted under `set -e` before any systemd action. Prior SQLite safe.
- [x] **M04-P SQL COMMA FIX AND RETRY CAP CODED:** corrected subscription SQL and allowed maximum one fresh gap-rebaseline retry per supervisor batch; added eighth targeted test for second gap. Not yet VPS accepted.
- [ ] **M04-P VPS PROOF PENDING:** run 8 M04-P + changed-scope E10/I7/J12 once, then guard actual stopped PUBLIC user service and saved epoch/offset/rows, one operator start. No blind restart, no deletion of historic SQLite or trading.
- [ ] **M04-C QUALIFIED G1 ISSUER STILL PENDING:** separate real source issuer, verified upstream origin, complete/appropriate trade evidence, safe replay/dedupe/expiry and historical ablation. Internal MAC alone cannot certify source provenance.
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
| M04-A VPS ACCEPTED (SHADOW, no provider) | SHA `232da715` user VPS | M03-I 8/8 and M04-A 10/10 tests PASS; real 20 pair 10 valid /10 rejected, 83 public GET, preview empty, ai_shadow no provider calls/roles, Final REJECT, exit0 | Real Sumopod models, G1 verified issuer/trade history, Final BUY and execution NOT proven |
| M04-B VPS ACCEPTED (MOCK, no actual AI service) | `553232b4` owner VPS output 26 Oct 2026 | 21/21 tests PASS (10 M04-A + 11 M04-B), real public 20 pair: 10 scored/10 rejected, 83 GET, warning flags all/preview [], ai_shadow NO_PROVISIONAL_CANDIDATE, roles=[], provider_connected=false, Final REJECT, exit0 | No live Sumopod inference, G1 issuer/trade-tape incomplete, no BUY/execution. |
| M04-C VPS ACCEPTED (internal receipt only) | owner VPS `84112ca5` | Targeted fixed M04-A PASS, M04-B 11/11 PASS, M04-C 10/10 PASS; public 20-pair BTC anomaly/reject, 83 GET and zero AI calls | Indodax upstream provenance and trade completeness still unproven; final echoed exit truncated |
| M04-D VPS ACCEPTED (PUBLIC source rejection) | SHA `cf68eb4e` VPS 26 Oct | 8/8 tests PASS; real public20 examined, 8 scored/12 rejected; 83 GET, 500 latest trades per pair but 0–7 in minute window; source admission rejects and Final REJECT, exit0 | No independently verifiable complete tape or real G1 issuer. |
| M04-E VPS ACCEPTED (auth/subscription only) | VPS SHA `14d64a15` | 10/10 tests PASS; real official public BTC/IDR auth+subscribe 12.09sec, epoch1633996541, offset14642052, zero trade publications, Final REJECT and exit0. | Real WS trade data, actual recover, full tape and G1 issuer NOT proven. |
| M04-F VERIFIED_PUBLIC_ONLINE (PUBLIC WS receive/restart) | owner VPS `513dc284` | Targeted ACK 1/1 PASS; two real BTC/IDR 30s sessions with heartbeat/subscribe true, received 2+1 live publications, total SQLite rows 2→4, offset14642460→14642462, same epoch, Final REJECT and exit0. | Exact recovery count inferred but not proven; live public WS is not complete certified tape. |
| M04-G VERIFIED_PUBLIC_ONLINE | VPS `037ce7e4` | 6/6 tests PASS; 27 PUBLIC WS replay publications newly persisted, no live frames, rows4→31, offset14642462→14642489, Final REJECT exit0. | Observed stream not complete authenticated exchange tape. |
| M04-H VPS SCOPED ACCEPTED | VPS `310944c7` | 8/8 tests PASS; real PUBLIC G1+WS BTC minute join rejected NO_ALIGNED_BTC_CLOSED_MINUTE, source unverified, no role calls, Final REJECT. | Actual same-minute join not proven, exit echo not supplied. |
| M04-I VPS ACCEPTED (bounded public WS) | owner VPS `8be8fbfb` | 7/7 PASS; three successful PUBLIC BTC WS sessions, 129 recovered+3 live, 163 SQLite total, offset14642489→14642621, Final REJECT exit0. | 24/7 service not enabled; full trade source verification missing. |
| M04-J VPS ACCEPTED (supervised PUBLIC batch only) | VPS `1dbe301d` | 9/9 unit PASS, PUBLIC 2/2 sessions, offset14642933, 0 persisted events pilot DB, Final REJECT, exit0. | systemd NOT installed/enabled; trade tape untrusted and source permission false. |
| M04-K VPS ACCEPTED (read-only last-batch health) | owner VPS `7b5e91cb` | 10/10 tests PASS; public 2/2 safe sessions, one persisted trade publication, epoch1633996541, offset14643217; read-only health `RECENT_COMPLETED_PUBLIC_BATCH_UNATTESTED` age0; Final REJECT exit0. | Health concerns historical batch only; no systemd status, no G1/AI/BUY permission. |
| M04-L VPS ACCEPTED (read-only systemd readiness) | VPS `7a5c4006` | 11/11 tests PASS 0.011s; real status `STAGED_NOT_INSTALLED`, staged valid, installed/enabled/active false, Final REJECT, exit0. | No user-systemd 24/7 service running; only staged deployment verified. |
| M04-M ACTIVE NEXT-BATCH HEALTH VPS ACCEPTED | owner VPS `ef2dccac` | 5/5 tests PASS; last checkpoint rows722/offset14644282, running batch rows742/offset14644302, epoch stable, health RECENT_COMPLETED_PUBLIC_BATCH_UNATTESTED; NRestarts10 active PID3025477, Final REJECT exit0. | Repeated public-data accumulation proven at snapshots, not full upstream trade coverage, approved AI or order. |
| M04-N VERIFIED_PUBLIC_ONLINE: REAL REST BTC CLOSED MINUTE ↔ LIVE WS SQLITE | VPS SHA `da796f4f`: 13/13 tests PASS, real `PUBLIC_SAMPLE_OBSERVED` BTC minute 1793081040, 5 trade rows in 4 publications, 1258 SHA integrity checks, service active NRestarts24, Final REJECT exit0. | `PUBLIC_BULK_INCOMPLETE_UNATTESTED` and unverified full minute tape remain explicit; no G1/AI/trade authority. | Next M04-O comparative source-quality diagnostic; do not rerun accepted tests. |
| M04-O VERIFIED_PUBLIC_ONLINE; LIVE WS SERVICE FAILED | Owner VPS SHA `babc6274`: 6/6 tests PASS and one real REST/WS quality comparison BTC closed start1793084820: REST 1 trade, WS 0 trades, visible BUY delta -255728.55096000 IDR, G1 false, AI [], Final REJECT, exit0. | **systemd ActiveState=failed, SubState=failed, NRestarts24** despite bulk command exit0. Prior collector snapshot NOT evidence of current health. | Targeted read-only systemd/journal/sidecar/SQLite triage before PUBLIC-only collector recovery. |
| M04-P GAP RECOVERY FIRST VPS FAIL 4/7, COMMA+RETRY FIX CODED, VPS PENDING | Owner 12/12 supervisor tests PASS; one restart still failed `WS_STREAM_GAP_OR_EPOCH_CHANGED`, SQLite1259 pubs/offset14644819 unchanged. | Gap now persisted during replay; append-only historical discontinuity marker + one bounded fresh public baseline retry added to existing collector, seven tests authored. | Need scoped VPS tests + one guarded service start. No complete tape, G1 issuer, AI, orders. |
| M04-Q–M12 | Belum ada acceptance | Tidak boleh dicentang atau dilabel selesai | Actual source qualification and independent G1 issuer, paid AIs, Telegram, capital, real execution and SELL |

**Catatan:** Tanggal baseline tidak berarti semua isi tersertifikasi hari itu. Ketika bukti baru diverifikasi, tambahkan **satu baris** sesuai milestone. Jika hanya perubahan status, jangan memodifikasi aturan trading di guideline.

## 5. FORMAT WAJIB SETELAH SETIAP TAHAP

Tulis ringkas di akhir sesi, dan perbarui checklist + checkpoint di sini:

1. **DIBUAT:** path kode/fitur aktual dan commit SHA.
2. **TERSAMBUNG:** caller → callee → bukti jalur utama; tulis jelas jika belum.
3. **DIPERIKSA:** tes per fungsi berubah, PASS/FAIL teramati, sumber real/mock/private, reference.
4. **BELUM:** error, blocker, izin atau tahapan yang belum tercapai; jangan menulis DONE bila tidak benar.
5. **NEXT SATU LANGKAH:** satu vertical slice yang paling mendekatkan pada bot scalping harian terintegrasi.

**Syarat selesai proyek akhir:** source scanner real, top candidates, MARKET/RISK/STRATEGY, PASS/REJECT MANUAL/AUTO, authenticated capital safe, idempotent BUY/SELL, protective exit, order/fill ownership, settled IDR dan realized net PnL; semua terbukti terhubung pada satu runtime dan ada persetujuan owner untuk LIVE. Tak ada jaminan Rp100.000 laba harian.
