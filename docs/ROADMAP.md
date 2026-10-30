# ROADMAP & EXECUTION CHECKLIST | INDODAX AI BOT V2

> **Satu-satunya tracker dan handover proyek.** Aturan normatif tetap di [PROJECT_GUIDELINE.md](PROJECT_GUIDELINE.md) bagian 0/0A/B/E/F serta [AGENTS.md](../AGENTS.md). Terakhir diperbarui: **30 Oktober 2026 (WIB)**.
>
> **Aturan pencatatan owner:** hanya catat pekerjaan/tes yang **sudah PASS dan diterima**, beserta SHA, cakupan, dan batas klaim. Jangan menyalin percobaan FAIL, error sementara, log mentah, audit berulang, atau status yang sudah digantikan. Satu penerimaan cukup satu entri ringkas. Riwayat lengkap tetap tersedia melalui Git history, bukan daftar kegagalan di tracker.
>
> **ATURAN BESI `[x]` = MAINPATH TERBUKTI (BERLAKU UNTUK CHECKLIST LAMA DAN BARU):** Setiap fitur kode/runtime hanya boleh dicentang setelah bukti menunjukkan caller resmi `systemd/Telegram/CLI → DailyScalpingOrchestrator → modul produksi yang SAMA → persistensi/output/Final REJECT atau PASS sesuai gate`, source SHA yang diuji, dan targeted integration test yang MELINTASI caller aslinya. Unit-test modul terisolasi, source file ada, PR merge, fixture/mock caller, atau laporan audit saja **tidak cukup**. Untuk klaim ONLINE harus dibuktikan pula exact installed source/unit dan eksekusi VPS real; OFFLINE tetap OFFLINE. Jika modul belum dipanggil jalur utama, **jangan `[x]` untuk integrasi**; simpan PASS tersendiri di register dan biarkan `[ ]` sampai tersambung. Tidak boleh menduplikasi scanner/AI/executor/position manager/ledger demi centang. M00 dokumen dan file CI sebagai *file* adalah pengecualian yang sifatnya non-runtime; kesiapan runner CI berada di M10. Centang historis tidak boleh otomatis dianggap bukti seluruh jalur end-to-end atau LIVE.

## 1. START HERE | Handover saat pindah chat

| Area | Status terkonfirmasi |
| --- | --- |
| **Misi** | DAILY spot scalping IDR: closed 1m + konteks 5m/15m/BTC → scanner top 20–40 → max 1–2 → MARKET → RISK veto → STRATEGY → Final MANUAL/AUTO PASS/REJECT → preflight → bot-owned BUY/SELL → IDR settled. Tidak ada kewajiban trade/hari; laba Rp100.000 net realized/hari adalah target, bukan jaminan. |
| **Current checkpoint** | **M03 ORIGINAL LIGHT+DEEP20 PUBLIC EXECUTION ACCEPTED, 30 Oktober 2026.** Empat tick LIGHT nyata dan satu DEEP20 nyata `attempted=20/analyzed=20`, `PARTIAL_INVALID_PAIR_EVIDENCE`, Final REJECT, `execution_allowed=false`. DEEP20 menggunakan mainpath/orchestrator asli, bukan scanner kedua. **Tidak berarti 20 pair valid atau G1 qualified.** |
| **Kode dan VPS** | GitHub `dvoriano/indodax-ai-bot-v2`; installed original M03 `~/indodax-ai-bot-v2-m03-runtime` SHA `eff8c8d944d9b875c0d0a4b34e6d731416d68d45` with PR#19 admission fail-closed and timer disabled. Installed original Telegram V2 `~/indodax-ai-bot-v2-m05-release` SHA `2e8e43357d73bdd8cce76de2ad208b15faf9f9a4` with timer enabled and scoped fail-closed startup PASS. Public BTC WS collector retains its separate checkout. New model default PR#21 and compact M03 telemetry PR#22 are draft/source only; not installed. |
| **Layanan terakhir diverifikasi** | BTC public WS collector **active**, Telegram V2 M05 timer **active**, M03 scanner timer **disabled/inactive**; V1/Hermes harus tetap dipisahkan dan tidak dimodifikasi. BTC recovery sempat membuktikan **satu batch sukses**, bukan jaminan uptime 24/7. |
| **Hardlocks** | `INDODAX_TRADING_ENABLED=NO`, `INDODAX_ALLOW_LIVE_EXECUTION=NO`, `INDODAX_EXECUTION_MODE=DRY_RUN`, Final REJECT; G1 public-unattested, **tidak ada izin paid AI, Indodax private API, BUY/SELL, maupun GO LIVE**. |
| **ONE NEXT ACTION** | [DRAFT PR#24](https://github.com/dvoriano/indodax-ai-bot-v2/pull/24) **24/24 original protected M03/M04 mainpath OFFLINE PASS** pinned `b7187ff16920e5e2f043610048b7cf1d4c3e1e04`. Next: safely stage *only original protected M03 runtime source* replacing `eff8c8d...` with pinned PR#24, timer disabled, exact unit unchanged, then prove missing-admission policy FAIL-CLOSED on installed source without public/private HTTP. Owner approval and rollback required. No G1 issuer, real complete tape, shared-egress all-peer accounting, paid AI, or live trading accepted. |

## 2. Status milestone M00–M12

| Milestone | Status yang sah | Cakupan diterima / gerbang tersisa |
| --- | --- | --- |
| **M00** Guideline | **DONE / DOCUMENTED** | Satu spesifikasi di `docs/PROJECT_GUIDELINE.md`, AGENTS dan README, satu tracker ini. |
| **M01** Python foundation | **DONE / TESTED_OFFLINE** | CLI → satu orchestrator → SQLite DecisionJournal, kontrak data, fail-closed dan policy. Bukan bukti order/CI. |
| **M02** Market public | **ADAPTER DONE / VERIFIED_PUBLIC_ONLINE** | REST Indodax publik closed 1m/5m/15m, ticker/trades/depth; bukan trade tape lengkap atau issuer G1. |
| **M03** Scanner & ranking | **PARTIAL / PUBLIC EXECUTION VERIFIED** | Scanner bulk 20 dan 40 pair diuji di publik; cadence LIGHT4 + DEEP20 satu-shot teruji. **OPEN:** 24/7 admission REST bersama, true valid candidate quality, calibrated net edge. |
| **M04** 3 role AI | **PARTIAL / M04 SOURCE DIAGNOSTICS 33/33 OFFLINE + GLM 27/27 OFFLINE** | [DRAFT PR#23](https://github.com/dvoriano/indodax-ai-bot-v2/pull/23) M04 source-consistency original mainpath **33/33 offline PASS** (no installed runtime). [DRAFT PR#21](https://github.com/dvoriano/indodax-ai-bot-v2/pull/21) owner-selected `glm-5.3-flash` 3 independent roles **27/27 offline PASS** (no real paid call). **OPEN:** combine diagnostics into PR#19 protected M03 source, real accepted public data, independently qualified G1 issuer/complete 15m+1h tape, provider account prices and separate paid GO. |
| **M05** Telegram Final | **PARTIAL / PR#20 ORIGINAL TELEGRAM REAL `/health` + `/scanner` PASS** | Owner VPS staged M05 release SHA `2e8e43357d73bdd8cce76de2ad208b15faf9f9a4`, 30s timer enabled; 2026-10-30 19:39 WIB owner screenshot shows genuine dedicated `@pitidenbot` private replies to separate `/health` and `/scanner`, both Final REJECT/Trading OFF and explicitly unauthenticated public observation. G1-qualified proposal, real PASS/REJECT buttons, and last-moment account/Risk/execution gates remain OPEN. |
| **M06** Account & capital | **NOT ACCEPTED** | Private account readonly, MAIN free IDR, FLOKI/PEPE reserve dan ownership butuh izin/sumber terverifikasi. |
| **M07** Executor | **NOT ACCEPTED** | Actual order/fill/preflight/idempotency/precision/reconciliation belum diterima; LIVE OFF. |
| **M08** Position/SELL | **PARTIAL / POLICY TESTED_OFFLINE, UNWIRED** | Fungsi ROI cost-basis dan waktu posisi 12/12 offline PASS, tetapi belum terhubung ke bot-owned Position Manager dalam mainpath. Karena itu, centang integrasi masih `[ ]`; actual fills, SELL, restart/IDR settlement belum terbukti. |
| **M09** End-to-end PAPER | **NOT ACCEPTED** | Full real-module shadow/PAPER pipeline tanpa real order masih terbuka. |
| **M10** VPS/CI release | **PARTIAL** | Public BTC collector + Telegram terpasang; CI Actions runner belum menghasilkan bukti green run, unified release/observability belum selesai. |
| **M11** LIVE readiness | **NOT ACCEPTED** | Independent issuer, account, order safety, lintas-bot guard dan izin owner terpisah mutlak. |
| **M12** Real pilot | **NOT ACCEPTED** | Belum ada V2 exchange-confirmed real BUY → SELL fills → settled IDR. |

**URUTAN KERJA OWNER (2026-10-30):** **Dependency-first M00–M11**. Kalau fitur M05/M08/M09 terhalang oleh gate M04 (G1 source/3 AI) atau M06/M07 (account/preflight/executor), kerjakan prerequisite yang sudah didefinisikan dahulu, baru kembali ke fitur terhalang. **Owner telah memilih tiga model `glm-5.3-flash`**, tetapi provider account availability, harga, paid GO dan real role response belum diterima. V1/Hermes dibiarkan apa adanya; migrasi/penonaktifan V1 hanya pada transisi akhir sesuai izin owner. M03 timer tetap disabled sampai IP egress-wide admission dan GO terpisah, tidak boleh bypass.

## 3. CHECKLIST PEKERJAAN M00–M12

**`[x]` = cakupan diterima; untuk fitur kode/runtime HARUS ada bukti terhubung ke mainpath asli. `[ ]` = belum selesai, belum dibuktikan, atau modul masih berdiri sendiri.** Angka bukan persentase kesiapan LIVE. Unit test-only PASS tetap di register bukti Bagian 4 tanpa mencentang integrasi. Jangan catat tes gagal.

| Milestone | Selesai `[x]` | Belum `[ ]` |
| --- | ---: | ---: |
| M00 | 4 | 0 |
| M01 | 4 | 0 |
| M02 | 3 | 1 |
| M03 | 13 | 5 |
| M04 | 10 | 6 |
| M05 | 8 | 4 |
| M06 | 0 | 5 |
| M07 | 0 | 5 |
| M08 | 0 | 7 |
| M09 | 0 | 4 |
| M10 | 3 | 4 |
| M11 | 0 | 5 |
| M12 | 0 | 5 |
| **TOTAL** | **45** | **51** |

### M00: Guideline, keamanan, arsitektur
- [x] Repo V2 independen, AGENTS/README, guideline kanonis dan satu roadmap.
- [x] Kebijakan DAILY scalping MAIN IDR, max 1–2 pair, FINAL PASS/REJECT MANUAL/AUTO diterima.
- [x] Aturan BUY ≥Rp10.000 **dan** ROI ≥2% netto, SELL 2/8/11% netto, cadangan FLOKI/PEPE ditetapkan.
- [x] Larangan live order tanpa GO terpisah, G1 issuer, verified balance dan risk gate ditetapkan.

### M01: Runtime foundation
- [x] CLI `python -m indodax_ai_bot`, typed contracts, Settings default NO/NO/DRY_RUN.
- [x] Satu orchestrator → SQLite DecisionJournal dengan durable REJECT dan idempotency.
- [x] Safety `--once`/`--health` dan scoped 7/7 offline tests PASS.
- [x] Workflow CI sudah dibuat sebagai source file; runner acceptance berada di M10.

### M02: Data publik Indodax
- [x] PUBLIC REST pair/ticker/depth/recent trades/closed 1m dan 15m reader; real BTC/IDR online observation.
- [x] 5m hanya dari lima candle 1m lengkap, data stale/ongoing ditolak.
- [x] SourceReceipt `verified=false`, public-only → orchestrator → Final REJECT, scoped 9/9 PASS.
- [ ] Verifikasi provenance independent dan full 15m/1h trade tape untuk G1 STRICT **di M04-S**, bukan mengulang tes M02.

### M03: Scanner, ranking, deep microstructure
- [x] **A–C:** all-IDR discovery/ranking shortlist30 max2; BTC/altcoin deep 1m/5m/15m; persistent rotating cursor. Scoped PASS.
- [x] **D–F:** same-window BTC regime, anomalous momentum, spread/book/RSI/flow quality rejection. Scoped PASS.
- [x] **G–I:** original 20–40 bulk with shared REST context, coverage reasons dan aligned eligible cohort. Scoped & real PUBLIC PASS.
- [x] **J–M:** 1m pattern/replay dan arsip nyata 4.320 closed BTC/IDR minutes; descriptive OOS accepted tanpa bukti net edge.
- [x] **N:** prospective early momentum + orderbook wall/surge casebook durable, 1.711 observations/1.616 matured labels.
- [x] Real public 40/40 pair diproses dengan 159 GET; tidak diklaim 40/40 data valid.
- [x] Original single scanner → orchestrator → BTC WS readonly/G1/M04 reject-only tersambung di SHA `d93cf2aa`.
- [x] Timer M03 disabled, source runtime dipisah dari BTC collector/V1 dan trading NO/NO/DRY_RUN.
- [x] **4 LIGHT + 1 DEEP20** real ticks accepted; DEEP20 20 attempted/20 analyzed, partial invalid, Final REJECT/execution false.
- [x] PR#18 diagnostic unit source PASS + terpasang nonaktif; PRE/POST systemd teramati dan JSON besar berhasil direkonstruksi.
- [ ] Implementasi fail-closed REST admission untuk semua proses dengan egress-IP sama (V1/Hermes/peers), bukan hanya V2 168/60s. PR#19 22/22 scoped offline PASS tetapi bukan bukti seluruh peer/IP terkoordinasi.
- [x] **Wiring PR#19 OFFLINE mainpath PASS (25/25)**: owner VPS pinned `eff8c8d944d9b875c0d0a4b34e6d731416d68d45`, 3/3 positive+negative integration tests `CLI scheduled M03 → original orchestrator → DailyPairScanner → IndodaxPublicMarket._get → egress admission → atomic SharedPublicRestBudget SQLite → DecisionJournal Final REJECT`; ditambah 9/9 admission + 7/7 quota + 6/6 cadence. Original unit template `systemd-analyze --user verify` PASS. **Tidak mencakup instalasi source baru atau IP-wide 24/7.**
- [x] **Staging nonaktif PR#19 pada ORIGINAL runtime M03 VPS PASS (2026-10-30)**: owner pin `eff8c8d944d9b875c0d0a4b34e6d731416d68d45`; `~/indodax-ai-bot-v2-m03-runtime` HEAD tepat SHA itu, installed user unit sama dengan `deploy/indodax-v2-m03-mainpath.service` dari commit PR#19; `systemd-analyze --user verify` PASS, `NeedDaemonReload=no`, timer M03 disabled, service inactive, BTC collector + Telegram V2 preserved, tidak ada scanner/order yang dieksekusi. Staging **bukan** izin online/24x7.
- [x] **Pemeriksaan fail-closed installed M03 source PASS (2026-10-30 WIB)**: pinned runtime `eff8c8d944d9b875c0d0a4b34e6d731416d68d45` dan installed user unit byte-exact cocok; CLI `--scheduled-public-scanner-once` dengan `INDODAX_M03_REQUIRE_EGRESS_ADMISSION=YES` serta file policy sengaja tidak ada menolak `M03_EGRESS_POLICY_UNAVAILABLE`, `HTTP_CALLS=0`, scanner DB tidak dibuat, `FINAL_EXECUTION=BLOCKED`; M03 timer/service inactive, BTC+Telegram active. **Tes CLI installed source, BUKAN service start atau verifikasi permintaan dari V1/peers.**
- [ ] Ringkas telemetry/output DEEP20 in original durable `DecisionJournal`, not stdout fragments. [DRAFT PR#22](https://github.com/dvoriano/indodax-ai-bot-v2/pull/22) source implemented on PR#19 pinned base; two new mainpath tests authored but **VPS not yet PASS**, no runtime staging.
- [ ] Kalibrasi setup momentum/pullback OOS, estimasi fee+slippage, edge net dan eligibility max2.
- [ ] Buktikan source G1 provenance/complete trade tape lewat M04 sebelum proposal AI/BUY.
- [ ] Uji dan izinkan timer M03 permanen hanya setelah IP admission dan owner GO baru, tanpa ulang bukti LIGHT/DEEP20.

### M04: Source G1 dan MARKET→RISK→STRATEGY
- [x] **A–D:** role contract, strict Risk veto, internal receipt verifier dan public source coverage gate; scoped PASS.
- [x] **E–L:** BTC public WS offset/replay/gap/SQLite source dan bounded supervisor/health; scoped + PUBLIC PASS.
- [x] **M–R:** user-systemd collector public, real supervised batch 10/10, minute REST/WS join, safe gap-aware source rejection.
- [x] **S/T:** test-only issuer injection dan immutable same-source role contexts untuk maks 2 candidates; offline PASS.
- [x] **U:** MARKET facts, RISK source-bound veto, STRATEGY net plan BUY Rp10k dan 2%+; offline PASS.
- [x] **V:** optional Sumopod provider adapter, 6-call limit, daily SQLite USD budget reservations; paid network OFF.
- [x] PR#7–#11 merged: freshness, frozen snapshot, MARKET facts, RISK and STRATEGY source-binding; scoped VPS PASS.
- [x] **M04 PR#23 source-consistency OFFLINE mainpath ACCEPTED (2026-10-30)**: pinned VPS `af3a0088de0fd63aeaa3c3756e00461fcea14d2f`, owner ran **5/5 new** original CLI→scanner→PublicMarket→bulk G1 admission→ThreeRoleShadow REJECT→actual SQLite Journal REJECT, **8/8 M04-D**, **11/11 M02 public adapter**, **9/9 M03-G** = **33/33 PASS**. Source-only [DRAFT PR#23](https://github.com/dvoriano/indodax-ai-bot-v2/pull/23); test mocks only public HTTP and clock; validates 1m/5m/15m OHLC consistency, ticker/server time skew, book ladder, UNKNOWN missing data. **No actual M03 runtime staging or real exchange observation/complete 15m+1h tape/issuer approval claimed.**
- [x] **M04/M03 combined PR#24 ORIGINAL MAINPATH OFFLINE ACCEPTED (2026-10-30)**: VPS pinned `b7187ff16920e5e2f043610048b7cf1d4c3e1e04`, 6/6 M04 + original scheduled DEEP20 with synthetic fresh per-peer policy and **83 real atomic SQLite budget reservations** to original G1 source rejection and `DecisionJournal`; 3/3 original REST egress mainpath, 9/9 peer guard, 6/6 cadence = **24/24 PASS**. Every HTTP response synthetic at transport boundary, time synthetic; owner output `M04_M03_EGRESS_G1_COMBINED_24_TESTS_PASS`, `M03_TIMER_DISABLED`, `PAID_AI_NOT_ENABLED`, `TRADING_NOT_ENABLED`. Original PR#19 `_get` guard unchanged, PR#24 [DRAFT](https://github.com/dvoriano/indodax-ai-bot-v2/pull/24), no runtime installation or actual exchange-source qualification claimed.
- [ ] **M04/M03 combined installed original M03 runtime (pending staging)**: install only PR#24 pinned source `b7187ff16920e5e2f043610048b7cf1d4c3e1e04` over currently installed M03 PR#19 `eff8c8d944d9b875c0d0a4b34e6d731416d68d45` in dedicated M03 checkout with disabled timer, unchanged installed unit and rollback. Verify missing-policy fail-closed on installed code, no network, no scanner start. Independently verifiable issuer/complete 15m+1h tape, real egress-IP peer accounting, and 24/7 timer GO remain separate OPEN prerequisites.
- [ ] Qualify independently verifiable issuer + **complete 15m/1h trade tape** and replay/gap coverage (owner Option A).
- [ ] Connect production G1 issuer/verifier with one original orchestrator and admit max2 genuine source-eligible candidates.
- [x] **Owner model defaults `glm-5.3-flash` for all 3 ROLES OFFLINE PASS (2026-10-30)**: [DRAFT PR#21](https://github.com/dvoriano/indodax-ai-bot-v2/pull/21), pinned VPS SHA `1a0860af69047e6bde9d3a8aea38bd222b88980d`; original public bulk CLI → orchestrator → ThreeRoleShadow safe source REJECT, one Sumopod adapter with 3 independently scoped MARKET/RISK/STRATEGY messages each selecting `glm-5.3-flash`. 12/12 adapter + 5/5 original CLI wiring + 10/10 role safety = **27/27 PASS**. Real network/paid adapter not invoked, actual model availability and output unverified.
- [ ] **Provider availability/pricing + real source-bound role responses**: confirm actual Sumopod account supports `glm-5.3-flash`, model identity/context/current prices and independent three responses only after qualified G1 + separate owner GO; safe default config does NOT establish provider connectivity or payable budget.
- [ ] Obtain separate owner authorization for paid AI, daily cost bound and secure credentials.
- [ ] Prove one real qualified source → 3 models → Risk veto/Strategy output → M05 handoff without BUY.

### M05: Final owner/Telegram
- [x] PR#12 MANUAL/AUTO shadow SQLite owner ledger, proposal TTL/replay-safety tests PASS.
- [x] PR#13–#14 Bot API transport and bot identity/webhook readonly preflight, scoped tests PASS.
- [x] PR#15 owner status send tested and real Telegram Bot API message delivered.
- [x] PR#16 dedicated `@pitidenbot` private owner inbound/persisted cursor, real `/status` response verified.
- [x] PR#17 separate 30s user timer/service installed with explicit GO, 8 real successful ticks and new owner command handled.
- [ ] Complete actual G1+AI proposal → owner PASS/REJECT inline buttons, max90s expiry/source binding.
- [ ] Prove callback single-use, replay guard, ownership and concurrent mode switching on real messages.
- [ ] Connect MANUAL/AUTO to same last-moment Risk/account/preflight gates, never bypass veto.
- [x] **M05 PR#20 `/scanner` + `/health` OFFLINE original Telegram mainpath accepted:** pinned VPS SHA `2e8e43357d73bdd8cce76de2ad208b15faf9f9a4`; 8/8 new service-tick→dedicated poller→owner-private gate→real handler→V2 read-only source→safe reply, plus 14/14 owner, 19/19 inbound, 19/19 tick = **60/60 PASS**. No live Telegram message, order, paid AI, private account, or new runtime install claimed.
- [x] **M05 PR#20 original runtime staging verified (2026-10-30)**: V2 Telegram dedicated `~/indodax-ai-bot-v2-m05-release` pinned `2e8e43357d73bdd8cce76de2ad208b15faf9f9a4`, original installed unit ExecStart unchanged, timer active + enabled after controlled pause, no overlapping poller, isolated service entrypoint with network GO=NO rejected as expected, M03 timer disabled, trading NO/NO/DRY_RUN. Owner VPS marker `M05_PR20_ORIGINAL_RUNTIME_STAGED_PASS`. **No real new Telegram message claimed.**
- [x] **M05 PR#20 REAL owner chat replies ACCEPTED (2026-10-30 19:39 WIB)**: owner screenshot of official dedicated `@pitidenbot` private chat shows individually submitted `/health` and `/scanner` at 19:39 receiving distinct V2 replies. `/health`: `BTC WS last batch=RECENT_COMPLETED_UNATTESTED`, M03 timer `INACTIVE (DISABLED)`, expressly NOT live heartbeat or trusted G1 source. `/scanner`: timer `INACTIVE`, `enabled=DISABLED`, service `INACTIVE`, expressly NOT valid market/cadence proof. Both replies end `Final REJECT. Trading OFF.` Prior combined newline `/scanner`+`/health` in ONE message was rejected with `Perintah belum tersedia. Trading OFF.` as designed for single-command handler; separate commands both PASS. Mainpath already pinned on installed M05 release SHA `2e8e43357d73bdd8cce76de2ad208b15faf9f9a4` and original timer. **This is real Telegram read-only status acceptance, not authenticated market tape, service uptime, cursor audit, paid AI, or trading permission.**
- [ ] Finish remaining owner Telegram commands `/market`, `/ai`, `/buy`, `/positions`, `/sell`, `/balance`, `/profit`, `/history`, `/risk` as verified actual V2 state or explicit fail-closed unavailable; source-bound G1/AI proposals and callbacks remain separately unchecked. Existing `/status` and `/kontrol` work through durable V2 ledger.

### M06: Account and protected capital
- [ ] Authenticated Indodax **private read-only** balance/orders/fills under explicit scoped GO.
- [ ] Verify fresh free IDR minus locked/unsettled commitment; atomic reserve, avoid double-spend.
- [ ] FLOKI 25% trading/75% reserve; PEPE 30% trading/70% reserve; manual holding exclusions.
- [ ] Protect rebuy bucket proceeds and bot-owned lot/fill attribution across restarts.
- [ ] Check cross-bot account balance/position ownership before LIVE coexistence.

### M07: Preflight & execution
- [ ] Official signed order/status/cancel adapter with hardlocked safe mode until LIVE GO.
- [ ] Validate pair tradability, precision, min notional, Indodax all-in fees, spread/slippage.
- [ ] Fresh source/Risk/owner/verified spendable IDR and idempotent order intent preflight.
- [ ] Resolve UNKNOWN, partial/cancel/fill, retry, duplicate protection and recovery from exchange.
- [ ] Never SELL over bot-owned verified quantity; scoped fault tests before authorization.

### M08: Position, SELL dan settled IDR
- [ ] Hubungkan policy net cost-basis T1/T2/T3 2/8/11% ke bot-owned Position Manager yang dipanggil mainpath; 12/12 scoped policy-only OFFLINE PASS tetap disimpan sebagai bukti modul.
- [ ] Hubungkan policy waktu posisi <30m/30–60m/≥60m/≥90m ke Position Manager asli dengan input actual bot-owned fills; offline policy-only PASS tetap tersimpan.
- [ ] Attach real exchange-confirmed BUY fills, owned lots, cost basis and fees to Position Manager.
- [ ] Implement protective SELL stop/trailing/time-stop, allowing loss when protection demands it.
- [ ] Implement tier SELL and ownership-limited partial fills/cancel/UNKNOWN/restart recovery.
- [ ] Reconcile actual SELL fills, settled IDR wallet delta and WIB realized net PnL.
- [ ] Post-SELL fresh rediscovery using same scanner, not automatic repurchase of previous coin.

### M09: Real-module shadow/PAPER end-to-end
- [ ] End-to-end real code modules: scanner→G1→three AI→Final→preflight→executor hardlock→SELL/IDR.
- [ ] Cover Risk veto, stale source, MANUAL/AUTO replay, quote/balance/precision and rate faults.
- [ ] Reconcile partial/unknown/restart, duplicate prevention and post-SELL re-discovery.
- [ ] Separate virtual PAPER PnL from exchange-confirmed real proceeds.

### M10: VPS operations, CI, telemetry
- [x] BTC public WS collector user-systemd active and real bounded batch success observed.
- [x] Dedicated Telegram V2 user-systemd timer active with real owner command receipt.
- [x] Original isolated M03 systemd service installed, source pinned and timer disabled.
- [ ] Produce CI GitHub Actions green PASS or approved equivalent independent release check.
- [ ] Complete shared egress IP REST rate admission and compact scanner journal telemetry.
- [ ] Prove stable runtime startup/stop/restart/heartbeat and sanitized alerts with 24/7 observation.
- [ ] Bring qualified public market+AI/Telegram into one pinned online mainpath with orders OFF.

### M11: LIVE risk release
- [ ] Prove real independently sourced G1 issuer and complete source coverage.
- [ ] Verify account ownership/spendable, fee/precision/order constraints and bot coexistence safety.
- [ ] Verify BUY→protective SELL→IDR reconciliation and crash/unknown recovery.
- [ ] Approve CI/release, credential security, drawdown/loss caps and kill-switch.
- [ ] Obtain **explicit separate owner GO** with max capital, order count and daily loss.

### M12: Limited real trading pilot
- [ ] Only after M11 GO, send bounded real BUY and verify exchange-confirmed order/fills.
- [ ] Prove protected target/protective SELL with bot-owned qty only.
- [ ] Reconcile SELL fills, actual all-in fee and settled IDR wallet change.
- [ ] Record realized net WIB PnL, costs, slippage, latency and risk statistics.
- [ ] Scale limits only with separate owner acceptance after completed safe pilot.

## 4. Register bukti **PASS yang diterima** (tanpa riwayat gagal)

**Cara membaca:** setiap entri hanya menyatakan cakupan yang benar-benar PASS; status SOURCE PUBLIC/TESTED_OFFLINE tidak pernah berubah menjadi LIVE secara otomatis. Jangan jalankan ulang tanpa perubahan terkait.

### M00–M03 | Fondasi, source, scanner publik

| Scope | SHA / jalur utama | Bukti PASS yang dipertahankan |
| --- | --- | --- |
| M00–M01 | [M01 `b73511e3`](https://github.com/dvoriano/indodax-ai-bot-v2/commit/b73511e38ec863bdc92618426a15e8e54371454d); `indodax_ai_bot/__main__.py`, `orchestrator.py`, `journal.py` | 7/7 fondasi offline + CLI `--once`/`--health` fail-closed. M00 dokumen diterima. |
| M02 public REST | `public_market.py`, `tests/test_m02_public_market.py`; VPS `7002387f` | 9/9 tests; satu real BTC/IDR public REST observation, closed candle parser; `SourceReceipt.verified=false`. |
| M03-A–F scanner awal | VPS `bdcd9f5`, `9bf4e3a`, `9e2744b`, `5f14be87`, `4cf220a7`, `40771ea7` | Full IDR discovery/ranking, rotating deep cursor, BTC same-window gate, anomaly dan quality caution; masing-masing scoped PASS (A9/9, B7/7, C7/7, D15/15, E29/29, F29/29). Public signal tetap diagnostik. |
| M03-G–I bulk/cohort | VPS `567eba1a`, `6d413428`, `c3fe498c` | Shared REST context, 20-pair public bulk, actual ~83 GET pada contoh, coverage reasons, aligned eligible subset; scoped 29/29, 26/26, 8/8. Bukan 20/20 valid atau G1 qualified. |
| M03-J–M research | VPS `41e7940f`, `f53a1f69`, `bf897434`; [PR#3 merged `0724190a`](https://github.com/dvoriano/indodax-ai-bot-v2/commit/0724190a6b76a7613596468279f36781a7f9eccc) | Closed-candle pattern checks, PUBLIC archive 4,320 contiguous BTC 1m bars, descriptive time-split/out-of-sample proxy; **tidak terbukti edge atau profit net**. PR#3 397/397 full offline PASS. |
| M03-N public casebook | Original `journal.py` / `deep_analysis.py`; database casebook owner VPS | Prospective ticker momentum, orderbook wall observations, 1,711 surge casebook observations with 1,616 matured 60m labels. Data observasional, bukan calibrated signal. |
| M03 real 40-pair and volume filter | Main SHA `fc6db759`, PR#2 merged `164cc59e7ff646074895c3bea21e64498256ed8f`; PR#4 merged `c53d0efc0e52862a8013d407d64378cec66db459` | Original real 40/40 attempted/analyzed, 159 GET, hanya 5 valid diagnostic/35 rejected; volume floor 24h Rp150m, same scanner. 403/403 scoped full offline PR#4 PASS. |
| M03 cadence source/unit | [PR#6 merged SHA `d93cf2aa`](https://github.com/dvoriano/indodax-ai-bot-v2/commit/d93cf2aa5ac2d70c8cc2b59af04a68f0b515b9a8), [DRAFT PR#18](https://github.com/dvoriano/indodax-ai-bot-v2/pull/18) diagnostic unit `49f304ef` | Staged isolated M03 original service, NO/NO/DRY_RUN, unit `systemd-analyze --user verify` PASS, no second scanner; original M03 timer disabled. |
| **M03 real cadence acceptance** | Owner VPS 2026-10-30, Python `d93cf2aa`, installed diagnostic unit `49f304ef` | **4 LIGHT ticks + 1 DEEP20 tick accepted**. DEEP20 exactly 20 attempted/20 analyzed, `PARTIAL_INVALID_PAIR_EVIDENCE`, `Final=REJECT`, `execution_allowed=false`; systemd PRE=1/POST=1, exit0. Oversize journal JSON had two `line-max` splits: `journalctl --all` and stream reassembly recovered one intact result. `screening=None` expected for bulk mode. BTC/M05 active; M03 timer disabled. |
| **M03 PR#19 original mainpath wiring OFFLINE ACCEPTED (2026-10-30 WIB)** | [DRAFT PR #19](https://github.com/dvoriano/indodax-ai-bot-v2/pull/19), head `eff8c8d944d9b875c0d0a4b34e6d731416d68d45`, stacked on DRAFT PR#18. Same `--scheduled-public-scanner-once` CLI → real `DailyScalpingOrchestrator.run_scan_cycle` → real `DailyPairScanner.scan` → real `IndodaxPublicMarket._get` → `M03SharedEgressAdmission` and existing `SharedPublicRestBudget.reserve` atomic SQLite → actual `DecisionJournal` / JSON Final REJECT. | Owner pinned detached VPS worktree `eff8c8d`: **3/3 original mainpath tests** (3 GET positive, 1 GET quota exhaustion, 1 GET policy expiry), **9/9 peer admission tests, 7/7 shared quota regressions, 6/6 original cadence = 25/25 PASS**, `systemd-analyze --user verify` PASS. Real SQLite recorded quota reservations and rejection, all HTTP responses synthetic at `_http_json` only; no public Indodax GET, no private/paid API/order. BTC/M05 observed active, M03 timer disabled, trading not enabled. **OFFLINE mainpath wiring CHECKED; subsequent VPS staging VERIFIED but no runtime scanner start. Real shared-egress external peer demand/IP unknown, 24/7 operation and trading NOT accepted.** |

| **M03 PR#19 original user-service staging nonaktif ACCEPTED (2026-10-30 WIB)** | Branch PR#19 pinned `eff8c8d944d9b875c0d0a4b34e6d731416d68d45`; installed `~/.config/systemd/user/indodax-v2-m03-mainpath.service` bytes match pinned source. | Owner VPS: `M03_PRECHECK_PASS`, `M03_PR19_SYSTEMD_VERIFY_PASS`, `M03_PR19_STAGED_DISABLED_PASS`, exact runtime SHA `eff8c8d`, `NeedDaemonReload=no`, M03 timer disabled, BTC+Telegram preserved, no scanner/trade executed. **No actual service start yet; later installed-source missing-policy smoke passed. Shared-IP peer demand still unverified.** |

| **M03 PR#19 installed-code missing-policy guard ACCEPTED (2026-10-30 WIB)** | Same original M03 runtime and systemd unit source SHA`eff8c8d944d9b875c0d0a4b34e6d731416d68d45` (no source changes). | Owner VPS output `M03_PINNED_INSTALLED_SOURCE_PASS`, `M03_INSTALLED_SOURCE_FAIL_CLOSED_PASS`, `M03_POLICY_MISSING_REJECT_PASS`, `HTTP_CALLS=0`, `SCANNER_DB_CREATED=NO`, `FINAL_EXECUTION=BLOCKED`, `M03_POLICY_FAILCLOSED_ACCEPTANCE_COMPLETE`. CLI injected missing file and mocked HTTP boundary; actual installed Python imported from pinned runtime; no scanner service started. BTC/M05 active, timer inactive/disabled. **Admission of other IP consumers and real IP-wide capacity still OPEN.** |

### M04 | Source guards, public WS dan tiga-role contract

| Scope | SHA / wiring | Bukti PASS yang dipertahankan |
| --- | --- | --- |
| M04-A–D | VPS `232da715`, `553232b4`, `84112ca5`, `cf68eb4e`; `three_role_ai.py`, `g1_receipt.py`, `source_coverage.py` | Role order MARKET → RISK veto → STRATEGY, internal receipt, public coverage safety; A10/10, B11/11, C10/10, D8/8 scoped accepted. **Mock/test-only issuer; paid roles not called.** |
| M04-E–L public BTC WS | VPS `513dc284`, `037ce7e4`, `310944c7`, `8be8fbfb`, `1dbe301d`, `7b5e91cb`, `7a5c4006` | Official public WS receive/offset/replay journal, read-only closed minute, bounded supervision, health and systemd introspection; scoped tests accepted. SQLite observed publications, NOT exhaustive tape. |
| M04-M–R public source safety | VPS `ef2dccac`, `da796f4f`, `babc6274`, `a93a6a67`, `e47e04f8`, `a243a53c`; existing BTC systemd collector | Real public supervised 10/10 batch proofs, REST/WS closed-minute diagnostic, integrity and gap-aware G1 rejection, deterministic failure labels. Source remains unverified, no AI authority. |
| M04-S/T/U/V internal role wiring | VPS `e890e4a8`, `eb3df7f`, `6a42476c`, `e274f89f`, `257784d4`, `f2aabf7` | Test-only issuer injection, max2 independent candidate contexts, net BUY floor >=Rp10k **AND** >=2%, strict RISK veto, Sumopod adapter CLI opt-in, six-call bound & SQLite micro-USD cost reservation. **No paid request.** |
| M04 PR#7–#11 merged contracts | [#7 `64d5b806`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/7), [#8 `ef805e81`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/8), [#9 `a217d50f`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/9), [#10 `42b85092`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/10), [#11 `a9eb8b69`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/11) | Public timeframe diagnostics, immutable source snapshot, MARKET fact catalog, RISK deterministic veto and STRATEGY source-bound net exit plan. Accepted VPS scoped offline tests only. Production G1 issuer, 15m/1h trade coverage and real 3 AI calls remain open. |

| **M05 PR#20 original Telegram V2 release STAGED (owner VPS 2026-10-30)** | Release worktree `~/indodax-ai-bot-v2-m05-release` SHA `2e8e43357d73bdd8cce76de2ad208b15faf9f9a4`; same originally installed `systemd/user/indodax-v2-m05-telegram.service` entrypoint. | Owner output `M05_SOURCE_INSTALLED`, `M05_OFFLINE_STARTUP_PASS`, `M05_TIMER_STATE=active`, `M05_TIMER_ENABLED=enabled`, `M05_PR20_ORIGINAL_RUNTIME_STAGED_PASS`, trading OFF and M03 timer disabled. **No real new /scanner or /health reply yet.** |

| **M04 PR#21 owner model `glm-5.3-flash` 3 roles OFFLINE ACCEPTED (2026-10-30 WIB)** | [DRAFT PR #21](https://github.com/dvoriano/indodax-ai-bot-v2/pull/21) pinned `1a0860af69047e6bde9d3a8aea38bd222b88980d`; 3 separate role prompts+responses in ORIGINAL Sumopod adapter and original CLI→orchestrator→source-only role safety. | Owner VPS **12/12 adapter + 5/5 CLI wiring + 10/10 three-role shadow = 27/27 PASS**, `M04_THREE_GLM53_FLASH_SCOPED_TESTS_PASS`, `THREE_ROLES_CONFIGURED`, `PAID_AI_NOT_ENABLED`, `TRADING_NOT_ENABLED`. Both fake transport and source rejection tested, no actual model account availability/pricing/paid request, no V2 runtime staging. |

| **M04 PR#23 original public source diagnostic mainpath OFFLINE ACCEPTED (2026-10-30 WIB)** | [DRAFT PR#23](https://github.com/dvoriano/indodax-ai-bot-v2/pull/23), pinned `af3a0088de0fd63aeaa3c3756e00461fcea14d2f`. Original CLI→scanner→PublicMarket observed server+ticker timing→M04-D G1 source admission→ThreeRoleShadow REJECT→SQLite DecisionJournal. | Owner VPS **5/5 new + 8/8 source coverage + 11/11 public adapter + 9/9 bulk = 33/33 PASS**; marker `M04_SOURCE_CONSISTENCY_MAINPATH_TESTS_PASS`, `NO_REAL_EXCHANGE_REQUEST`, `PAID_AI_NOT_ENABLED`, `TRADING_NOT_ENABLED`. Verified in detached isolated worktree only; NOT installed in M03 PR#19 protected runtime. Actual G1 source/issuer and trade tape not proven. |

| **M04+M03 PR#24 protected original DEEP20 OFFLINE ACCEPTED (2026-10-30 WIB)** | [DRAFT PR#24](https://github.com/dvoriano/indodax-ai-bot-v2/pull/24), pinned VPS SHA `b7187ff16920e5e2f043610048b7cf1d4c3e1e04`, stacked on PR#19 original systemd M03 path with unchanged egress `_get`. | Owner **6/6 M04 G1 source timing/OHLC/depth + original DEEP20 admission (83 mocked HTTP→real SQLite reservations) + 3/3 original egress mainpath + 9/9 peer guard + 6/6 cadence = 24/24 PASS**. Verified on detached VPS worktree; HTTP synthetic, original DecisionJournal Final REJECT. Source not yet installed, no real exchange tape/issuer, timer disabled and trading/paid AI OFF. |

### M05/M08 | Owner control dan position policy

| Scope | SHA / wiring | Bukti PASS yang dipertahankan |
| --- | --- | --- |
| M05 PR#12–#14 | [#12 `810de665`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/12), [#13 `8d58b598`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/13), [#14 `48f402f3`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/14) | Owner MANUAL/AUTO ledger 36/36, Bot API transport 33/33, readonly bot identity/webhook 32/32 accepted OFFLINE; no execution authority. |
| M05 PR#15–#17 | [#15 `45627533`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/15), [#16 `0533a174`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/16), [#17 `fb1bb29a`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/17) | Owner status sender 28/28, dedicated V2 inbound owner gate 44/44, systemd entrypoint/poller 39/39 + 2/2 final targeted tests accepted. |
| M05 real private Telegram owner | `@pitidenbot`, dedicated isolated release and 30s timer; owner VPS 2026-10-30 | Real getMe/webhook preflight, incoming owner `/status`, owner SQLite command handled/offset advanced, real Telegram reply witnessed, eight successful automatic scheduled poll ticks. Bot V2 separated from old V1/Hermes; still only status/owner view, not executable PASS/BUY. |
| **M05 PR#20 original Telegram V2 read-only mainpath OFFLINE ACCEPTED (2026-10-30 WIB)** | [DRAFT PR #20](https://github.com/dvoriano/indodax-ai-bot-v2/pull/20), pinned `2e8e43357d73bdd8cce76de2ad208b15faf9f9a4`; original `telegram_service_tick.service_tick_once → dedicated_owner_poll_once → TelegramOwnerPoller._dispatch → OfflineTelegramOwnerInterface → m05_operator_views.readonly_operator_view`; `/health` reuses original `public_ws_health`, `/scanner` reads only M03 systemd status. | Owner VPS **8/8 new + 14/14 owner ledger + 19/19 dedicated inbound + 19/19 Telegram service tick = 60/60 PASS**. Non-owner rejected, AUTO does not authorize order, restart does not replay, /buy remains blocked, unavailable health not falsely shown active. Bot API synthetic only; test source checked out in temporary pinned worktree. **Subsequently installed on original runtime; real separate command replies ACCEPTED by owner screenshot at 19:39 WIB.** |
| **M05 PR#20 original V2 Telegram owner `/health` + `/scanner` ONLINE READ-ONLY ACCEPTED (2026-10-30 19:39 WIB)** | Installed independent M05 release pinned `2e8e43357d73bdd8cce76de2ad208b15faf9f9a4` after prior 60/60 original service-tick offline tests and `M05_PR20_ORIGINAL_RUNTIME_STAGED_PASS`. | Owner screenshot: dedicated @pitidenbot responded at 19:39 to `/health` with BTC WS `RECENT_COMPLETED_UNATTESTED` and M03 timer inactive/disabled, then `/scanner` with M03 timer/service inactive/disabled. Both explicitly Final REJECT, Trading OFF and no G1 promotion. Earlier two commands in a single message returned blocked as expected. **No order/AI/private access or 24/7 scanner proof; Telegram SQLite cursor not inspected in this screenshot.** |
| M08 net exit policy | VPS SHA `f159925`; `ScalpingPolicy.assess_position_exit_targets`, `.assess_position_holding_time` | 12/12 scoped offline PASS for actual-cost-basis-derived hypothetical 2%/8%/11% net targets and time-priority (<30m,30–60m,>=60m,>=90m). Actual bot-owned fills/SELL not wired. |

## 5. Gerbang yang **belum** memenuhi acceptance

Ini adalah **daftar pekerjaan/risiko yang masih terbuka**, bukan jurnal percobaan yang gagal.

- **M03 operasi 24/7:** satu coordinator/admission untuk semua pemakai REST yang berbagi egress IP, termasuk V1/peers, plus rate telemetry, bounded output dan explicit owner timer GO. Guard V2 168/60s bukan jaminan IP-wide.
- **M04 G1 + AI nyata:** asal penerbit source terverifikasi independen, source-receipt 15m/1h/trade-tape lengkap, max2 valid source-bound, tiga model tersedia, harga/token/budget disetujui, real three-role PASS/VETO proof. Tetap STRICT Option A, bukan promosi data PUBLIC.
- **M05 produksi:** Telegram proposal sah dengan callback one-use PASS/REJECT terikat candle/proposal TTL <=90s, AUTO tetap tunduk Risk/preflight; perintah status tidak menyiratkan kesiapan BUY.
- **M06–M09:** akun private read-only hanya dengan GO terpisah; free IDR dan ownership FLOKI/PEPE; executor/order lifecycle, protected SELL, settlement, full shadow/PAPER. Modul tidak boleh mengirim order sebelum gerbang lengkap.
- **M10–M12:** CI runner/release automation memerlukan bukti PASS (atau pengganti setara yang disetujui), keamanan dua bot satu akun, owner explicit LIVE GO, kemudian real limited BUY→SELL→IDR exchange-confirmed pilot. Bukan tugas yang boleh dipercepat karena M03 PASS.

## 6. Cara memperbarui tracker ini

1. **Saat tes gagal:** perbaiki di branch/PR; jangan menambah FAIL ke roadmap. Unit-test PASS tetapi tidak ada caller mainpath = tetap `[ ]` integrasi.
2. **Sebelum setiap centang `[x]` fitur runtime:** WAJIB tampilkan path & pinned SHA yang dipakai, **caller resmi → orchestrator → modul produksi → Final/ledger/output**, bukti targeted positive/negative integration test yang benar-benar mengeksekusi caller itu, dan untuk klaim ONLINE bukti installed unit serta runtime real. Bila satu mata rantai tidak terbukti, **jangan dicentang**, walaupun semua unit tests PASS. Pengecualian hanya checklist dokumentasi/berkas non-runtime yang memang sudah didefinisikan demikian.
3. **Setelah integration PASS:** simpan **satu** entri ringkas Bagian 4 berisi SHA, jalur mainpath, jenis OFFLINE/PUBLIC/PRIVATE/PAPER/LIVE, hasil aktual, batas klaim; perbarui checklist Bagian 3 dan checkpoint Bagian 1 jika diterima. Milestone PARTIAL tetap PARTIAL sampai acceptance lengkap, bukan menumpuk audit.
4. **Jangan tandai DONE hanya karena unit test/simulasi lulus.** Model real, source verified, saldo, fills dan settled IDR harus benar-benar dibuktikan sesuai milestone.
5. **Setelah satu tahap implementasi:** laporkan (a) kode, (b) wiring mainpath, (c) proof+SHA, (d) yang belum, (e) persis satu next action. Fokus bot daily scalping, bukan audit tanpa akhir.

**NEXT (satu):** stage previously-tested PR#24 source to original `~/indodax-ai-bot-v2-m03-runtime` only after exact old/new SHA and installed user unit diff guards; preserve BTC collector, M05 Telegram timer, shared-egress policy and M03 timer disabled. Test absence of admission policy with synthetic offline CLI afterwards. PR#24 offline **24/24** accepted; actual source trust and 15m/1h exhaustive trade tape still OPEN.
