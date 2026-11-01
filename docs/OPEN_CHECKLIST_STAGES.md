# TRACKER 49 CHECKLIST TERBUKA | M00–M12

> Tambahan status untuk checklist terbuka di [ROADMAP.md](ROADMAP.md). 49 checklist historis `[x]` tidak disentuh. ID per milestone mengikuti urutan seluruh item lama, termasuk yang `[x]`. Target menjelang LIVE: validasi gabungan 98/98. Lima gerbang tidak membuat scope baru.

> **Centang hanya sesuai bukti.** Kode = perubahan source tersedia; Audit = tes relevan lulus; Terhubung bot = original caller/mainpath teruji; Deploy = exact source/unit VPS nyata terverifikasi (jika tidak relevan, beri N/A dengan alasan); Selesai = acceptance item asli terpenuhi. Setiap bukti menyebut SHA dan batas klaim. OFFLINE tidak berarti ONLINE; source staged bukan deploy. Trading tetap NO/NO/DRY_RUN sampai GO terpisah.

### M02-04 · **M02 → M04-S G1 STRICT: BLOCKED_UPSTREAM_ATTESTED_TAPE**. Verifikasi provenance independen dan *seluruh* trade tape window 15m/1h. **M02 public adapter/1m/5m/15m OHLC sudah selesai dan tidak perlu tes ulang.** Dokumen resmi Indodax [Public REST](https://github.com/btcid/indodax-official-api-docs/blob/master/Public-RestAPI.md) memuat recent trades dan OHLC tf15/tf60 (bukan semua tick); [Marketdata WS](https://github.com/btcid/indodax-official-api-docs/blob/master/Marketdata-websocket.md) memuat trade-activity offset/recover (bukan attestation exhaustive/full-window). Belum ditemukan **supported documented** upstream signed completeness/issuer receipt untuk 15m/1h. **Tidak boleh `[x]` atau G1 ACCEPT** sebelum bukti upstream tersedia; lanjutkan pekerjaan independen lain tanpa mengulang M02/M03/M04 A–R.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M03-11 · Implementasi fail-closed REST admission untuk semua proses dengan egress-IP sama (V1/Hermes/peers), bukan hanya V2 168/60s. PR#19 22/22 scoped offline PASS tetapi bukan bukti seluruh peer/IP terkoordinasi. **Koreksi PR#27 (31 Okt, CODED / BELUM TESTED COMBINED):** Tambahan pengecekan `systemctl` unit V1/Hermes dan 6 tesnya **DIHAPUS** dari source V2; mekanisme PR#19/25 `M03SharedEgressAdmission` dan `SharedPublicRestBudget` tetap dipakai tanpa membaca service/repo luar. Perubahan baru pada ORIGINAL CLI `--scheduled-public-scanner-once` menolak `INDODAX_M03_REQUIRE_EGRESS_ADMISSION` yang `NO`/hilang **sebelum journal/scanner/HTTP**, dan policy valid dituntut saat YES. Satu test regresi bypass ditambahkan di `tests/test_m03_safe_minute_cadence.py` dan fixture cadence lama disesuaikan. **Perlindungan semua pemakai egress-IP belum terbukti**; publik REST V2 masih blocked bila policy absent/invalid/stale, dan M03 timer tetap disabled. Tes terbaru belum berjalan karena GitHub Actions gagal sebelum steps (runner kosong). **Jangan centang sebelum external IP scope + original caller proof diterima.**
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M03-17 · Kalibrasi setup momentum/pullback OOS, estimasi fee+slippage, edge net dan eligibility max2.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M03-18 · Buktikan source G1 provenance/complete trade tape lewat M04 sebelum proposal AI/BUY.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M03-19 · Uji dan izinkan timer M03 permanen hanya setelah IP admission dan owner GO baru, tanpa ulang bukti LIGHT/DEEP20.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M04-12 · **M04-S ACCEPTANCE G1 STRICT (BLOCKED_EXTERNAL_EVIDENCE)**: terima **bukti issuer upstream independen yang otentik** + complete trade-by-trade 15m **dan** 1h per pair, batas jendela awal/akhir, pagination/coverage tanpa pemotongan, replay/epoch/offset/gap continuity, konsistensi trade→OHLC dan freshness, diikat ke source receipt/candidate dalam original orchestrator. TLS, recent REST, candle tf15/tf60, WS offset berurutan atau HMAC issuer INTERNAL sendirian **tidak mencukupi**. Sumber yang belum memiliki attestation tetap `REJECT_SOURCE_NOT_QUALIFIED` dan paid AI/order OFF. **NEXT bukti eksternal yang dibutuhkan:** endpoint/ekspor yang didukung Indodax atau penyedia independen **dengan jaminan full-window dan identitas/attestation yang dapat diverifikasi**; baru implementasi adapter/checker pada jalur yang SUDAH ADA, bukan membangun collector baru.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M04-13 · Connect production G1 issuer/verifier with one original orchestrator and admit max2 genuine source-eligible candidates.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M04-15 · **Provider availability/pricing + real source-bound role responses**: confirm actual Sumopod account supports `glm-5.3-flash`, model identity/context/current prices and independent three responses only after qualified G1 + separate owner GO; safe default config does NOT establish provider connectivity or payable budget.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M04-16 · Obtain separate owner authorization for paid AI, daily cost bound and secure credentials.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M04-17 · Prove one real qualified source → 3 models → Risk veto/Strategy output → M05 handoff without BUY.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M05-06 · Complete actual G1+AI proposal → owner PASS/REJECT inline buttons, max90s expiry/source binding.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M05-07 · Prove callback single-use, replay guard, ownership and concurrent mode switching on real messages.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M05-08 · Connect MANUAL/AUTO to same last-moment Risk/account/preflight gates, never bypass veto.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M05-12 · Finish remaining owner Telegram commands `/market`, `/ai`, `/buy`, `/positions`, `/sell`, `/balance`, `/profit`, `/history`, `/risk` as verified actual V2 state or explicit fail-closed unavailable; source-bound G1/AI proposals and callbacks remain separately unchecked. Existing `/status` and `/kontrol` work through durable V2 ledger.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

**Mainline consolidation accepted (1 November 2026):** owner VPS executed `test_m06_*.py` on exact PR#31 head `8b6b84dd897c2265ea32be5c92bd3b4fbe0af5de`: **112/112 PASS in 4.830s**, plus original V2 `--m06-account-review-once` CLI printed `REJECT`, `account_authenticated=false`, `execution_allowed=false`, marker `M06_MAINLINE_CONSOLIDATION_PASS`. [PR#31](https://github.com/dvoriano/indodax-ai-bot-v2/pull/31) **squash-merged to main** as `a01b0c445ae4bccff0bbcfce8508855b50b6ed46`, with 34 M06-scoped changed files and no M03/M04/M05/M10 modifications. GitHub CI recorded no status; do not claim CI PASS. Owner VPS mainline checkout/install and service-level deployment have NOT been attested. No additional Indodax private GO or exchange action permitted.

**VPS main checkout installation verified by owner (1 November 2026):** clean tracking `main` checkout at `~/indodax-ai-bot-v2` safely fast-forwarded from `db95103` to `cf978e396ab03a2ef89f01a4cf886f8c2d94cc33` (contains M06 squash commit `a01b0c445ae4bccff0bbcfce8508855b50b6ed46`), marker `M06_V2_MAIN_SOURCE_INSTALLED_SAFE`. Original V2 CLI `--m06-account-review-once` returned `status=REJECT`, `reason=M06_PRIVATE_ACCOUNT_GO_ABSENT`, `account_authenticated=false`, `execution_allowed=false`. **The 84-file fast-forward output is accumulated old-main→new-main history, not 84 changes from PR#31.** This proves main checkout/source is installed and CLI is callable, **not** systemd unit linkage, automated run-cycle invocation, foreign bot cessation, authenticated account-wide capital or completed M06 deployment. Existing M03 service template uses a separate pinned worktree; M05 service template uses root V2 checkout; live installed unit state still unknown. No service/timer change, new private GO, network request or trade occurred.

### M06-01 · Authenticated Indodax **private read-only** balance/orders/fills under explicit scoped GO.
- [x] Kode. One-shot scoped signed V2 read-only source in [PR#29](https://github.com/dvoriano/indodax-ai-bot-v2/pull/29), pinned observed SHA `f21d9224638f9646c17a6b4c34059d4a1d2b383e`, 4 GET only, no executor.
- [x] Audit. Owner previously supplied **23/23 offline PASS**; 2026-11-01 WIB owner VPS provided **REAL VIEW ONLY** result `M06_VIEW_ONLY_RECONCILED_REJECT`, `view_only_permissions_verified=true`, `execution_allowed=false`; no raw private data published. This is a bounded session, not full history or capital trust.
- [x] Terhubung bot. Original V2 CLI `--m06-private-readonly-once` → `DailyScalpingOrchestrator` → signed 4-GET account/openOrders/injidr order-history/injidr myTrades → reconciler and journal; owner output `M06_VIEW_ONLY_COMPLETED`. Observed open_orders_count=0, order_history_count=0, fills_count=0 for the defined scope; `history_complete=false`.
- [x] Deploy. Scoped source merged into GitHub V2 main via PR#31, source installed on owner VPS root `main` at `cf978e3`. The explicit one-shot original CLI path was previously executed on owner VPS with owner GO and returned `M06_VIEW_ONLY_RECONCILED_REJECT`; no background private polling is required or authorized.
- [x] Selesai. **M06-01 ONLY:** real owner-approved four scoped read-only GETs completed; `view_only_permissions_verified=true`; original V2 parser/reconciler and journal correctly returned non-tradable REJECT. M06-02–05 capital, manual portfolio, full-history ownership and external co-use acceptance are SEPARATE OPEN items. This M06-01 acceptance is not permission for a new API session or a trade.

**Owner VPS additional focused acceptance (1 November 2026):** [PR#32](https://github.com/dvoriano/indodax-ai-bot-v2/pull/32), tested pinned feature head `62f96f9042a57d197deacf14dd48344b20cf91af`, **12/12 PASS in 0.934s**, `M06_ONE_ACCOUNT_DIAGNOSTIC_TEST_PASS` (6 new single-account-diagnostic + 6 existing private UID-binding). **Squash-merged to GitHub `main`** as `a9008351f3bc5af82eec284c58f3398403f37967`; owner VPS checkout **not yet fast-forwarded to this new commit**. One existing original V2 reconciler now computes a consolidated account-observation preview: free IDR excludes exchange-locked IDR already; subtract locally known protected rebuy and local shadow commitments exactly once; report remaining amount as **unverified upper bound**, never spendable/BUY permission. Missing peer liabilities and ownership are UNKNOWN, not zero. The original orchestrator mock four-GET path still rejects. **No extra owner approval layer or separate service was added**; manual owner PASS/REJECT remains the last human decision for a future eligible order, automatic technical limits remain. Real authenticated balances and exchange-wide liabilities are not proven complete; M06-02 `Selesai` stays `[ ]`. This scoped acceptance is not equivalent to CI or production runtime deployment.

### M06-02 · Verify fresh free IDR minus locked/unsettled commitment; atomic reserve, avoid double-spend.
Evidence 1 November 2026: owner VPS pinned `13ae5de746065763c97977cb28d6cd0756c022fe`, six new M06 UID-bound local shadow commitments tests all PASS, including local atomic replay/release, three-way concurrent reserve, invalid ledger fail-closed and original V2 four-mocked-GET route. The one unrelated ConnectBot Enter regression was corrected at `8329af1db18cce374b84ad5a1e4c83988befeffe` and owner VPS accepted **6/6 PASS** (`M06_CONNECTBOT_ENTER_FIX_PASS`). Local capital accounting is integrated into original V2; it still **does not prove exchange-wide unsettled commitments**, other bots' balance liabilities or true spendable wallet IDR. Only the limited code/audit/wiring acceptance is checked; actual deployment and full M06-02 acceptance remain open.
- [x] Kode. Existing V2 SQLite account-hash-scoped `m06_shadow_reserve` is read in one read-only UID snapshot; active sums are fed to capital diagnostics, missing table means coverage unknown.
- [x] Audit. Six new capital tests PASS at owner VPS (among the earlier 39 successful tests), and related ConnectBot input regression independently 6/6 PASS.
- [x] Terhubung bot. Original `DailyScalpingOrchestrator` four-mocked-GET source → `m06_account_review` → `reconcile_unverified_capital` still REJECTs with no exchange authorization.
- [ ] Deploy. Consolidated source is installed in owner VPS main and offline CLI REJECT verified; still no proof of service invocation or complete live-account capital gates.
- [ ] Selesai. Fresh, complete real exchange and cross-bot obligations/ownership and safe true available IDR are NOT verified.

### M06-03 · FLOKI 25% trading/75% reserve; PEPE 30% trading/70% reserve; manual holding exclusions.
Owner VPS scoped `M06_SPECIAL_POOL_ORIGINAL_MAINPATH_PASS`, exact source SHA `a8a275974ea3703e7822fbecac3101cf57f2018d`, **28/28 tests PASS in 2.429s** on 1 November 2026. Seven M06-03 tests plus six UID shadow, six UID binding, nine protected rebuy ownership. FLOKI 25/75 and PEPE 30/70 quantities are enforced as theoretical ceilings, account-UID-bound manual floors can only tighten reserves, over-wallet manual floor rejects, absent manual coverage never proves ownership. **Actual bot-owned sellable quantity remains ZERO**; no SELL rights and no deployment inferred. Only scoped code/audit/wiring acceptance is checked.
- [x] Kode. Original V2 `m06_account_review.review_m06_special_partitions` builds fixed protected ratios and applies UID/manual floor monotonicity.
- [x] Audit. Owner VPS 28/28 scoped source and regressions PASS at exact SHA `a8a275974ea3703e7822fbecac3101cf57f2018d`.
- [x] Terhubung bot. Original V2 `DailyScalpingOrchestrator` mocked four GETs reaches the same account review, emits only redacted Boolean coverage and final REJECT.
- [ ] Deploy. M06 protective source installed in VPS main; no evidence of live systemd M06 invocation or exchange-verified ownership.
- [ ] Selesai. No real bot-owned lot provenance, authorized special SELL or rebuy settlement evidence.

### M06-04 · Protect rebuy bucket proceeds and bot-owned lot/fill attribution across restarts.
Owner VPS 1 Nov 2026 accepted `Ran 17 tests in 1.323s\nOK` and marker `M06_FILL_RECEIPTS_ORIGINAL_MAINPATH_PASS` at exact PR#29 SHA `c324273d89e32dc00950558500fa76693791963d`. Eight new UID-bound immutable local fill receipt tests plus nine original protected rebuy tests PASS. New receipt hashes account UID scope, local order/client IDs, side, fill amount/fees/quote in the original SQLite transaction; replay/restart/foreign UID conflicts reject. Legacy unlinked fills remain protected but cannot silently gain provenance. **This is LOCAL evidence only**: bot-owned quantity, external fills, sale settlement, main IDR capital and exchange-confirmed ownership are still unverified; no production deployment.
- [x] Kode. Original V2 `M06OwnershipLedger` stores immutable local fill receipts atomically with existing observed fills and FLOKI/PEPE rebuy protection.
- [x] Audit. Owner VPS accepted 17/17 scoped tests on exact source `c324273d89e32dc00950558500fa76693791963d`.
- [x] Terhubung bot. Original `DailyScalpingOrchestrator` four mocked GETs → original account review/CLI; reports only `local_rebuy_receipts_linked` and final REJECT.
- [ ] Deploy. Original V2 main checkout is now updated and M06 CLI rejects safely; service binding and exchange-owned lot proof not established.
- [ ] Selesai. Exchange-confirmed bot-owned lots, fees, settlement, and rebuy attribution not proven.

### M06-05 · Check cross-bot account balance/position ownership before LIVE coexistence.
Owner VPS 1 November 2026 at exact PR#29 source SHA `790f0fddde2c9c43c5bf1d0113bcb911589b593d`: **13/13 scoped offline PASS in 1.318s**, marker `M06_CROSS_BOT_LOCAL_GUARD_PASS`. Seven new `tests.test_m06_local_coexistence_guard` and six existing `tests.test_m06_private_uid_binding` tests PASS. Same original V2 UID-bound SQLite `mode=ro` snapshot detects foreign account-hash records, shadow commitments and orphan local fill observations; original `DailyScalpingOrchestrator` with mocked four GETs returns redacted flags and REJECT. **Absence of local conflicts is not evidence that other accounts, peers, V1, manual sessions or external clients no longer access the same exchange wallet**; all external peer coverage, spendable capital and trading rights remain FALSE. No V1 repo/process reads or real private calls.
- [x] Kode. Original V2 account ownership ledger has local other-UID and unattributed-fill risk indicators without new bots or services.
- [x] Audit. Owner VPS original source 13/13 tests PASS at SHA `790f0fddde2c9c43c5bf1d0113bcb911589b593d`; no exchange requests or permission escalation.
- [x] Terhubung bot. Original orchestrator → read-only account diagnostic and CLI expose Boolean-only coexistence flags; always final REJECT.
- [ ] Deploy. Root checkout installed and CLI REJECT verified; original V2 unit link/automatic M06 execution and external peer controls not yet inspected.
- [ ] Selesai. Actual exchange-wide shared-account liabilities, external peer controls and bot-owned position proof are outstanding.

### M07-01 · Official signed order/status/cancel adapter with hardlocked safe mode until LIVE GO.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M07-02 · Validate pair tradability, precision, min notional, Indodax all-in fees, spread/slippage.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M07-03 · Fresh source/Risk/owner/verified spendable IDR and idempotent order intent preflight.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M07-04 · Resolve UNKNOWN, partial/cancel/fill, retry, duplicate protection and recovery from exchange.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M07-05 · Never SELL over bot-owned verified quantity; scoped fault tests before authorization.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M08-01 · Hubungkan policy net cost-basis T1/T2/T3 2/8/11% ke bot-owned Position Manager yang dipanggil mainpath; 12/12 scoped policy-only OFFLINE PASS tetap disimpan sebagai bukti modul.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M08-02 · Hubungkan policy waktu posisi <30m/30–60m/≥60m/≥90m ke Position Manager asli dengan input actual bot-owned fills; offline policy-only PASS tetap tersimpan.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M08-03 · Attach real exchange-confirmed BUY fills, owned lots, cost basis and fees to Position Manager.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M08-04 · Implement protective SELL stop/trailing/time-stop, allowing loss when protection demands it.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M08-05 · Implement tier SELL and ownership-limited partial fills/cancel/UNKNOWN/restart recovery.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M08-06 · Reconcile actual SELL fills, settled IDR wallet delta and WIB realized net PnL.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M08-07 · Post-SELL fresh rediscovery using same scanner, not automatic repurchase of previous coin.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M09-01 · End-to-end real code modules: scanner→G1→three AI→Final→preflight→executor hardlock→SELL/IDR.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M09-02 · Cover Risk veto, stale source, MANUAL/AUTO replay, quote/balance/precision and rate faults.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M09-03 · Reconcile partial/unknown/restart, duplicate prevention and post-SELL re-discovery.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M09-04 · Separate virtual PAPER PnL from exchange-confirmed real proceeds.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M10-04 · Produce CI GitHub Actions green PASS or approved equivalent independent release check.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M10-05 · Complete shared egress IP REST rate admission and compact scanner journal telemetry.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M10-06 · Prove stable runtime startup/stop/restart/heartbeat and sanitized alerts with 24/7 observation.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M10-07 · Bring qualified public market+AI/Telegram into one pinned online mainpath with orders OFF.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M11-01 · Prove real independently sourced G1 issuer and complete source coverage.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M11-02 · Verify account ownership/spendable, fee/precision/order constraints and bot coexistence safety.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M11-03 · Verify BUY→protective SELL→IDR reconciliation and crash/unknown recovery.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M11-04 · Approve CI/release, credential security, drawdown/loss caps and kill-switch.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M11-05 · Obtain **explicit separate owner GO** with max capital, order count and daily loss.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M12-01 · Only after M11 GO, send bounded real BUY and verify exchange-confirmed order/fills.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M12-02 · Prove protected target/protective SELL with bot-owned qty only.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M12-03 · Reconcile SELL fills, actual all-in fee and settled IDR wallet change.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M12-04 · Record realized net WIB PnL, costs, slippage, latency and risk statistics.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M12-05 · Scale limits only with separate owner acceptance after completed safe pilot.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai
