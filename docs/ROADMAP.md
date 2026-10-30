# ROADMAP & EXECUTION CHECKLIST | INDODAX AI BOT V2

> **Satu-satunya tracker dan handover proyek.** Aturan normatif tetap di [PROJECT_GUIDELINE.md](PROJECT_GUIDELINE.md) bagian 0/0A/B/E/F serta [AGENTS.md](../AGENTS.md). Terakhir diperbarui: **30 Oktober 2026 (WIB)**.
>
> **Aturan pencatatan owner:** hanya catat pekerjaan/tes yang **sudah PASS dan diterima**, beserta SHA, cakupan, dan batas klaim. Jangan menyalin percobaan FAIL, error sementara, log mentah, audit berulang, atau status yang sudah digantikan. Satu penerimaan cukup satu entri ringkas. Riwayat lengkap tetap tersedia melalui Git history, bukan daftar kegagalan di tracker.

## 1. START HERE | Handover saat pindah chat

| Area | Status terkonfirmasi |
| --- | --- |
| **Misi** | DAILY spot scalping IDR: closed 1m + konteks 5m/15m/BTC → scanner top 20–40 → max 1–2 → MARKET → RISK veto → STRATEGY → Final MANUAL/AUTO PASS/REJECT → preflight → bot-owned BUY/SELL → IDR settled. Tidak ada kewajiban trade/hari; laba Rp100.000 net realized/hari adalah target, bukan jaminan. |
| **Current checkpoint** | **M03 ORIGINAL LIGHT+DEEP20 PUBLIC EXECUTION ACCEPTED, 30 Oktober 2026.** Empat tick LIGHT nyata dan satu DEEP20 nyata `attempted=20/analyzed=20`, `PARTIAL_INVALID_PAIR_EVIDENCE`, Final REJECT, `execution_allowed=false`. DEEP20 menggunakan mainpath/orchestrator asli, bukan scanner kedua. **Tidak berarti 20 pair valid atau G1 qualified.** |
| **Kode dan VPS** | GitHub repo `dvoriano/indodax-ai-bot-v2`; scanner Python isolated `~/indodax-ai-bot-v2-m03-runtime` pada SHA `d93cf2aa5ac2d70c8cc2b59af04a68f0b515b9a8`. Diagnostic systemd unit dari [DRAFT PR #18](https://github.com/dvoriano/indodax-ai-bot-v2/pull/18) SHA `49f304efd9a624f93a792e00c2ef6ba63f743dd5` terpasang di VPS, belum merged. Source original collector checkout jangan diganti demi M03. |
| **Layanan terakhir diverifikasi** | BTC public WS collector **active**, Telegram V2 M05 timer **active**, M03 scanner timer **disabled/inactive**; V1/Hermes harus tetap dipisahkan dan tidak dimodifikasi. BTC recovery sempat membuktikan **satu batch sukses**, bukan jaminan uptime 24/7. |
| **Hardlocks** | `INDODAX_TRADING_ENABLED=NO`, `INDODAX_ALLOW_LIVE_EXECUTION=NO`, `INDODAX_EXECUTION_MODE=DRY_RUN`, Final REJECT; G1 public-unattested, **tidak ada izin paid AI, Indodax private API, BUY/SELL, maupun GO LIVE**. |
| **ONE NEXT ACTION** | **Implementasikan gerbang admission REST untuk IP keluar bersama sebelum meminta izin owner mengaktifkan timer M03 permanen.** Guard SQLite V2 `168 request/rolling 60s` hanya mengoordinasikan klien V2; permintaan REST dari V1/Hermes/proses/IP lain belum tercakup. Jangan ulang bukti M03 LIGHT/DEEP20 yang sudah PASS. |

## 2. Status milestone M00–M12

| Milestone | Status yang sah | Cakupan diterima / gerbang tersisa |
| --- | --- | --- |
| **M00** Guideline | **DONE / DOCUMENTED** | Satu spesifikasi di `docs/PROJECT_GUIDELINE.md`, AGENTS dan README, satu tracker ini. |
| **M01** Python foundation | **DONE / TESTED_OFFLINE** | CLI → satu orchestrator → SQLite DecisionJournal, kontrak data, fail-closed dan policy. Bukan bukti order/CI. |
| **M02** Market public | **ADAPTER DONE / VERIFIED_PUBLIC_ONLINE** | REST Indodax publik closed 1m/5m/15m, ticker/trades/depth; bukan trade tape lengkap atau issuer G1. |
| **M03** Scanner & ranking | **PARTIAL / PUBLIC EXECUTION VERIFIED** | Scanner bulk 20 dan 40 pair diuji di publik; cadence LIGHT4 + DEEP20 satu-shot teruji. **OPEN:** 24/7 admission REST bersama, true valid candidate quality, calibrated net edge. |
| **M04** 3 role AI | **PARTIAL / OFFLINE CONTRACT + PUBLIC WS** | MARKET/RISK/STRATEGY typed/shadow, frozen source, provider adapter/budget, public WS + G1 safe rejection. **OPEN:** source issuer+complete 15m/1h, paid real models dan full three-role handoff. |
| **M05** Telegram Final | **PARTIAL / REAL OWNER COMMAND VERIFIED** | SQLite owner ledger dan dedicated V2 `@pitidenbot` polling 30s serta real `/status` owner ditangani. **OPEN:** proposal sah, callback PASS/REJECT, full owner-facing features & production state. |
| **M06** Account & capital | **NOT ACCEPTED** | Private account readonly, MAIN free IDR, FLOKI/PEPE reserve dan ownership butuh izin/sumber terverifikasi. |
| **M07** Executor | **NOT ACCEPTED** | Actual order/fill/preflight/idempotency/precision/reconciliation belum diterima; LIVE OFF. |
| **M08** Position/SELL | **PARTIAL / POLICY TESTED_OFFLINE** | Kalkulator ROI net cost-basis dan waktu posisi 12/12 PASS; actual owned fills, protective SELL, restart/IDR settlement belum tersambung. |
| **M09** End-to-end PAPER | **NOT ACCEPTED** | Full real-module shadow/PAPER pipeline tanpa real order masih terbuka. |
| **M10** VPS/CI release | **PARTIAL** | Public BTC collector + Telegram terpasang; CI Actions runner belum menghasilkan bukti green run, unified release/observability belum selesai. |
| **M11** LIVE readiness | **NOT ACCEPTED** | Independent issuer, account, order safety, lintas-bot guard dan izin owner terpisah mutlak. |
| **M12** Real pilot | **NOT ACCEPTED** | Belum ada V2 exchange-confirmed real BUY → SELL fills → settled IDR. |

## 3. Register bukti **PASS yang diterima** (tanpa riwayat gagal)

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

### M04 | Source guards, public WS dan tiga-role contract

| Scope | SHA / wiring | Bukti PASS yang dipertahankan |
| --- | --- | --- |
| M04-A–D | VPS `232da715`, `553232b4`, `84112ca5`, `cf68eb4e`; `three_role_ai.py`, `g1_receipt.py`, `source_coverage.py` | Role order MARKET → RISK veto → STRATEGY, internal receipt, public coverage safety; A10/10, B11/11, C10/10, D8/8 scoped accepted. **Mock/test-only issuer; paid roles not called.** |
| M04-E–L public BTC WS | VPS `513dc284`, `037ce7e4`, `310944c7`, `8be8fbfb`, `1dbe301d`, `7b5e91cb`, `7a5c4006` | Official public WS receive/offset/replay journal, read-only closed minute, bounded supervision, health and systemd introspection; scoped tests accepted. SQLite observed publications, NOT exhaustive tape. |
| M04-M–R public source safety | VPS `ef2dccac`, `da796f4f`, `babc6274`, `a93a6a67`, `e47e04f8`, `a243a53c`; existing BTC systemd collector | Real public supervised 10/10 batch proofs, REST/WS closed-minute diagnostic, integrity and gap-aware G1 rejection, deterministic failure labels. Source remains unverified, no AI authority. |
| M04-S/T/U/V internal role wiring | VPS `e890e4a8`, `eb3df7f`, `6a42476c`, `e274f89f`, `257784d4`, `f2aabf7` | Test-only issuer injection, max2 independent candidate contexts, net BUY floor >=Rp10k **AND** >=2%, strict RISK veto, Sumopod adapter CLI opt-in, six-call bound & SQLite micro-USD cost reservation. **No paid request.** |
| M04 PR#7–#11 merged contracts | [#7 `64d5b806`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/7), [#8 `ef805e81`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/8), [#9 `a217d50f`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/9), [#10 `42b85092`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/10), [#11 `a9eb8b69`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/11) | Public timeframe diagnostics, immutable source snapshot, MARKET fact catalog, RISK deterministic veto and STRATEGY source-bound net exit plan. Accepted VPS scoped offline tests only. Production G1 issuer, 15m/1h trade coverage and real 3 AI calls remain open. |

### M05/M08 | Owner control dan position policy

| Scope | SHA / wiring | Bukti PASS yang dipertahankan |
| --- | --- | --- |
| M05 PR#12–#14 | [#12 `810de665`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/12), [#13 `8d58b598`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/13), [#14 `48f402f3`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/14) | Owner MANUAL/AUTO ledger 36/36, Bot API transport 33/33, readonly bot identity/webhook 32/32 accepted OFFLINE; no execution authority. |
| M05 PR#15–#17 | [#15 `45627533`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/15), [#16 `0533a174`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/16), [#17 `fb1bb29a`](https://github.com/dvoriano/indodax-ai-bot-v2/pull/17) | Owner status sender 28/28, dedicated V2 inbound owner gate 44/44, systemd entrypoint/poller 39/39 + 2/2 final targeted tests accepted. |
| M05 real private Telegram owner | `@pitidenbot`, dedicated isolated release and 30s timer; owner VPS 2026-10-30 | Real getMe/webhook preflight, incoming owner `/status`, owner SQLite command handled/offset advanced, real Telegram reply witnessed, eight successful automatic scheduled poll ticks. Bot V2 separated from old V1/Hermes; still only status/owner view, not executable PASS/BUY. |
| M08 net exit policy | VPS SHA `f159925`; `ScalpingPolicy.assess_position_exit_targets`, `.assess_position_holding_time` | 12/12 scoped offline PASS for actual-cost-basis-derived hypothetical 2%/8%/11% net targets and time-priority (<30m,30–60m,>=60m,>=90m). Actual bot-owned fills/SELL not wired. |

## 4. Gerbang yang **belum** memenuhi acceptance

Ini adalah **daftar pekerjaan/risiko yang masih terbuka**, bukan jurnal percobaan yang gagal.

- **M03 operasi 24/7:** satu coordinator/admission untuk semua pemakai REST yang berbagi egress IP, termasuk V1/peers, plus rate telemetry, bounded output dan explicit owner timer GO. Guard V2 168/60s bukan jaminan IP-wide.
- **M04 G1 + AI nyata:** asal penerbit source terverifikasi independen, source-receipt 15m/1h/trade-tape lengkap, max2 valid source-bound, tiga model tersedia, harga/token/budget disetujui, real three-role PASS/VETO proof. Tetap STRICT Option A, bukan promosi data PUBLIC.
- **M05 produksi:** Telegram proposal sah dengan callback one-use PASS/REJECT terikat candle/proposal TTL <=90s, AUTO tetap tunduk Risk/preflight; perintah status tidak menyiratkan kesiapan BUY.
- **M06–M09:** akun private read-only hanya dengan GO terpisah; free IDR dan ownership FLOKI/PEPE; executor/order lifecycle, protected SELL, settlement, full shadow/PAPER. Modul tidak boleh mengirim order sebelum gerbang lengkap.
- **M10–M12:** CI runner/release automation memerlukan bukti PASS (atau pengganti setara yang disetujui), keamanan dua bot satu akun, owner explicit LIVE GO, kemudian real limited BUY→SELL→IDR exchange-confirmed pilot. Bukan tugas yang boleh dipercepat karena M03 PASS.

## 5. Cara memperbarui tracker ini

1. **Saat tes gagal:** perbaiki di branch/PR dan bahas di chat/issue seperlunya; **jangan menambah baris FAIL** ke roadmap.
2. **Saat bukti akhir PASS:** perbarui satu entri di Bagian 3 dengan path/commit SHA, caller→callee, angka hasil aktual, tipe bukti (OFFLINE/PUBLIC/PRIVATE/PAPER/LIVE), dan batas klaim. Jika mengubah checkpoint aktif, perbarui Bagian 1.
3. **Saat milestone belum selesai:** cukup tampilkan status PARTIAL/NOT ACCEPTED dan satu gerbang yang masih perlu ditutup di Bagian 2/4, tanpa mengulang 10 diagnosis atau menyalin log.
4. **Jangan tandai DONE hanya karena unit test/simulasi lulus.** Model real, source verified, saldo, fills dan settled IDR harus benar-benar dibuktikan sesuai milestone.
5. **Setelah satu tahap implementasi:** laporkan (a) kode, (b) wiring mainpath, (c) proof+SHA, (d) yang belum, (e) persis satu next action. Fokus bot daily scalping, bukan audit tanpa akhir.

**NEXT (satu):** implementasi koordinasi/gerbang REST shared-egress untuk M03 tanpa mengaktifkan timer, mengubah BTC collector/V1/Hermes, memanggil paid AI/private Indodax atau mengizinkan order.
