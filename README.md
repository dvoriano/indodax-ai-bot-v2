# Indodax AI Bot V2

**MISI UTAMA: SCALPING HARIAN INDODAX (24/7 market monitoring, intraday spot trading IDR → BUY → SELL → IDR).** Bot baru untuk spot scalping Indodax, dibangun modular **dari nol** dan **terpisah dari bot lama**. Target akhir: market scanner G1, 3 AI (MARKET → RISK → STRATEGY), Final Decision PASS/REJECT via Telegram MANUAL/AUTO, modal IDR dinamis, protective FLOKI/PEPE, BUY → SELL → IDR reconciliation, monitoring, dan operasi VPS yang aman.

> **Status, bukti, blocker dan satu milestone aktif selalu dibaca dari [ROADMAP.md](docs/ROADMAP.md).** M03-I accepted for 8/8 VPS tests and real aligned 8-of-20 public cohort. M04-A shadow source guard passed 18/18 scoped VPS tests and real 20-pair no-provider run. M04-B Sumopod mock adapter passed 11/11 scoped VPS tests; M04-A 10/10. M04-C independent G1 issuer receipt guard CODED, not yet VPS-proven. Paid models and trading remain OFF. Telegram, capital and executor are not enabled. **Trading LIVE OFF.** Target laba bukan jaminan.

## Mulai di sini

- **[PROJECT GUIDELINE & MASTER CHECKPOINT](docs/PROJECT_GUIDELINE.md)** — **satu sumber kebenaran** untuk seluruh fitur, keputusan owner, arsitektur, acceptance criteria, roadmap, dan progres pengembangan.
- **[ROADMAP & CHECKLIST](docs/ROADMAP.md)** — status pekerjaan yang bisa dicentang, bukti SHA, blocker, milestone aktif dan handover pindah chat. **Satu-satunya tracker kemajuan.**
- **[AGENTS.md](AGENTS.md)** — instruksi singkat untuk coding agent supaya selalu mengikuti milestone aktif dan tidak mengulang pekerjaan.

## IRON RULES

**Jangan hanya audit atau membuat modul yang belum tersambung.** Setelah menetapkan arsitektur, kontrak, struktur repository dan modul utama, implementasikan satu vertical slice lalu lanjut milestone. Setiap tahap harus menunjukkan **(1) kode aktual, (2) wiring, (3) bukti, (4) pending, (5) next action**. Guideline untuk kebijakan, roadmap untuk tracking, no repeat accepted PASS, tanpa bypass keamanan LIVE. Detail lengkap di Bagian **0 dan 0A** guideline.

## Workflow pengembangan

Baca guideline 0/0A/B/E/F → **ROADMAP 1/3/4** → kerjakan **satu** milestone → sambungkan ke mainpath → tes affected scope → update checklist/evidence di **ROADMAP.md** → lanjut.

**Milestone aktif M03-H:** M03-G VPS 29/29 scoped tests PASS and 20 public pair observations completed (12 valid diagnostics, 8 rejected), Final REJECT. M03-H per-pair rejected evidence and measured GET counter CODED, pending VPS proof. Handover in ROADMAP. Semua private API, provider berbayar, dan trading LIVE wajib otorisasi terpisah dan bukti verifikasi sesuai guideline.

**Repo lama:** dvoriano/crypto-bot adalah referensi saja. Jangan menyalin secrets, posisi atau ledger, dan jangan menjalankan dua executor LIVE pada akun yang sama tanpa koordinasi yang sah.

## Menjalankan fondasi M01 (NON-LIVE)

```bash
python -m unittest discover -s tests -v
python -m indodax_ai_bot --db /tmp/indodax-v2.sqlite3 --once
python -m indodax_ai_bot --db /tmp/indodax-v2.sqlite3 --health
```

Pada M01, tanpa G1 source sah keputusan selalu `REJECT`; `execution_allowed=false`. `INDODAX_TRADING_ENABLED=YES`, `INDODAX_ALLOW_LIVE_EXECUTION=YES` atau mode `LIVE` ditolak saat startup. Belum ada koneksi private/exchange maupun paid AI.

## M02: Observasi Indodax PUBLIK (no order)

Hanya pada checkout repository V2 terkini, dengan koneksi internet ke Indodax:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN \
  python3 -m indodax_ai_bot --db /tmp/indodax-v2-m02-public.sqlite3 --verify-public --pair btc_idr
```

Perintah memakai hanya `/api/pairs`, `/api/server_time`, `/api/ticker/<id>`, `/api/trades/<id>`, `/api/depth/<id>` dan `/tradingview/history_v2` dari dokumentasi resmi. Candle 5m dibentuk dari lima candle 1m yang lengkap. Keputusan tetap `REJECT` dengan `execution_allowed=false` karena G1 issuer dan coverage trade belum diotorisasi. Jangan melabel output publik sebagai sinyal BUY.

**M02 strict source probe:** `--verify-public` exit 0 means all required public payloads parsed (`PUBLIC_OBSERVED_G1_UNATTESTED`); exit 3 means public source error. Both cases return `final_decision=REJECT`, `execution_allowed=false` and `live_trading_authorized=false`. Exit 0 does not certify G1 source issuer or allow LIVE trading.

## VPS V2 tanpa menyentuh bot lama

Keputusan owner: **jangan bersihkan/reset VPS lama sekarang**. V2 wajib memakai folder, SSH deploy key read-only, dan runtime terpisah. [Script bootstrap V2](ops/bootstrap_v2_vps.sh) mempunyai dua aksi:

```bash
# Dari checkout V2 (atau gunakan petunjuk setup key awal di chat jika belum bisa clone)
bash ops/bootstrap_v2_vps.sh prepare-key
# Daftarkan PUBLIC key pada repo V2 Settings > Deploy keys (READ ONLY).
bash ops/bootstrap_v2_vps.sh sync-and-probe
```

Tidak menghapus folder, menghentikan service, menyentuh credential Indodax, atau mengaktifkan LIVE. M02_EXIT_CODE=0 hanya membuktikan data publik terbaca, tidak mengesahkan sinyal G1 maupun izin trading.

## M03-A: Uji scanner awal pada VPS (tanpa order)

Setelah `git pull --ff-only origin main` pada checkout V2, jalankan hanya scoped test baru dan satu scan publik:

```bash
INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN \
  python3 -B -m unittest discover -s tests -p 'test_m03a_scanner.py' -v
INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03a.sqlite3 --scan-once
```

Scanner hanya memakai endpoint publik Indodax `/api/pairs`, `/api/server_time`, `/api/ticker_all` dan membuat shortlist awal. Volume 24 jam bukan sinyal scalp yang cukup: **G1 issuer tetap unverified, Final REJECT, order OFF**. M03-B akan mengonfirmasi candle 1/5/15m, BTC, trade-flow dan bukti microstructure sebelum 3 AI.

## M03-B: Verifikasi deep-scoring publik, NON-LIVE

MAIN kini mengecualikan PEPE/FLOKI (dana khusus) dan USDT/USDC (stablecoin settlement). `--deep-once` memakai satu scanner dan maksimal dua deep observations dengan EMA8/21, RSI14, volume, spread, visible flow/book. Ini hasil analisis observasi, **bukan** rekomendasi BUY.

Pada VPS checkout V2 terbaru, jalankan tes scope yang berubah, bukan audit seluruh kode:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m0[23]*.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03b.sqlite3 --deep-once
```

Deep pilot hanya 2 pair, **belum** 20-40 full coverage. Visible trade sample tidak berarti trade history lengkap. G1 issuer tetap unverified, Final REJECT dan eksekusi selalu OFF.


## M03-C: rotasi deep public 2 pair/siklus (belum LIVE/PAPER)

Mode `--rolling-deep-once` memakai **satu scanner dan satu analyzer** yang sudah ada, dengan cursor SQLite dan batas 2 pair per eksekusi; default minimum antarawal scan 12 detik. Reservasi atomik, bisa dilanjutkan setelah restart, dan mencegah dua proses memulai batch serentak. Setiap siklus tetap `REJECT`, data historis antarwaktu **tidak boleh dicampur menjadi ranking live**.

Jalankan pada V2 VPS dengan environment DRY_RUN:

```bash
python3 -B -m unittest discover -s tests -p 'test_m03c_rotation.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03c.sqlite3 --rolling-deep-once
```

Output sukses public-only: `PUBLIC_ROLLING_UNATTESTED`, `reserved_pairs`, `next_index`, `cross_cycle_ranking_allowed=false`, `final_decision=REJECT` dan `execution_allowed=false`. Jika dijalankan kembali dalam kurang dari 12 detik, akan `PUBLIC_ROLLING_BLOCKED`/exit3. Ini tidak memanggil model AI/akun pribadi/order.

## M03-D: Same-minute BTC regime alignment, public-only

Updated existing rolling path, no new scanner or AI. Every pair and BTC context must have the same **closed 1m epoch** and timely observations, otherwise `same_window.status` is a REJECT reason and the list of synchronized diagnostics is empty. An aligned result is still **PUBLIC UNATTESTED**, not qualified for G2, never an execution permission.

Check changed tests on VPS after pulling current SHA:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m03[c-d]*.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03d-samewindow.sqlite3 --rolling-deep-once
```

Do not mix candidate scores from different one-minute candle closes. Historical coverage across 20-40 assets is future work, not evidence of one synchronous decision. Final remains REJECT.

## M03-E: provisional extreme-momentum review gate (non-live)

VPS M03-D 15/15 tests PASS on 5f14be87. Real FARTCOIN/IDR last-minute zero volume was rejected, while BR/IDR yielded diagnostic score 80 with RSI100, 82.89x volume, sampled visible sells much larger than sampled buys. M03-E adds review flags to **existing** deep_analysis, not a new scanner or BUY condition.

Provisional cutoffs: last closed-minute volume >=10x preceding 10-min average combined with RSI >=90 and/or visible sell notional >=3x sampled buy. This is conservative anomaly screening (not evidence of fraud, not an empirically optimized probability). An anomalous candidate gets score `null`, `REJECT_MOMENTUM_ANOMALY`, `anomaly_flags`, and cannot pass same-window diagnostic ranking. Thresholds must be historically replayed before reliance for trading.

One scoped VPS check after updating repository:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m03[b-e]*.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03e.sqlite3 --rolling-deep-once
```

No G1 provenance, trade coverage, AI, Telegram or live exchange permission is enabled by this milestone.

## M03-F: observable quality cautions and a non-trading preview

VPS M03-E reached **29/29 PASS**, real aligned public BTC+ETH source and `M03E_EXIT_CODE=0` at 4cf220a7. BTC regime then was DOWNTREND and the pair diagnostics were both score30, with negative book imbalance. These are timestamped samples, not real-time guidance.

M03-F adds provisional caution flags to the **same** analyzer: BTC downtrend, RSI <=30 / >=75, 1m relative volume below 1, book imbalance below -0.5, or sampled visible sells above buys. A synchronized public-only preview may include up to 2 candidates with zero caution flags. This preview is NOT a qualified G1 receipt or a trading signal, all guard booleans remain false. Cutoffs have not been validated by historical replay.

One VPS verification of affected tests and public market:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m03[d-f]*.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03f.sqlite3 --rolling-deep-once
```

Actual trade history coverage and G1 issuer are NOT present; Final Decision remains REJECT, private keys and orders OFF.

## M03-G: Optimized public 20–40 source observations, no-trade

Official Indodax Public REST API documents **180 requests/minute**: https://github.com/btcid/indodax-official-api-docs/blob/master/Public-RestAPI.md. Current per-pair adapter historically fetched 7 GET per pair, too expensive for 30+. **New shared-context path** reuses three scanner responses (pairs/server_time/ticker_all), leaving four per-pair GET: OHLC 1m/15m, public trades, depth. For 20 pairs estimate 83 GET (or 87 if BTC outside batch); for 40 163 (or 167 extra BTC). Count is only a single-cycle estimate and does NOT include other processes on the shared VPS public IP. Keep conservative 12 GET reserve.

`--bulk-deep-once` defaults to **20** and stops collecting after 30 seconds from scanner observation. Results are only comparable if every selected pair is valid and aligned to the same closed 1m/5m/15m context with BTC. Otherwise `same_window.status` rejects and any synchronized ranking/preview remains empty. `trade_history_complete=false`, `g1_verified=false`, Final REJECT, order OFF regardless of success.

Only after a clean Git pull on VPS, test changes to M02 adapter, M03-A scanner, and new M03-G:

```bash
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m02_public_market.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m03a_scanner.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -p 'test_m03g_bulk.py' -v
env INDODAX_TRADING_ENABLED=NO INDODAX_ALLOW_LIVE_EXECUTION=NO INDODAX_EXECUTION_MODE=DRY_RUN PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m indodax_ai_bot --db /tmp/indodax-v2-m03g-bulk.sqlite3 --bulk-deep-once
```

These are exactly the three affected scopes: M02 public adapter, M03-A scanner and M03-G bulk. Previously accepted M03-B/C/D/E/F are not rerun unless they break due to an actual dependency change. 20 live public pair requests can still fail or time out; this is not a scanner malfunction by itself. Do NOT enable orders.

## M03-H: why each public candidate was rejected

M03-G VPS run processed 20/20 candidates with 12 diagnostic scores and 8 rejects. Source not trusted and synchronized ranking rejected despite process exit0. Previous output showed only top-10 scores, making rejected-pair causes invisible.

M03-H enriches the same code path with `candidate_rejection_reasons`, `candidate_rejection_reason_counts`, `coverage_status`, `not_attempted_pairs`, `attempted_without_result_count` and `public_get_attempt_count_this_client`. Counting is **per single client**, not shared-IP/global, and does not add public GETs. The partial probe status now says `PUBLIC_BULK_INCOMPLETE_UNATTESTED`; Final REJECT and order OFF.

Verify only affected M02 adapter, M03-G bulk and M03-H suites on the VPS before one real public 20-pair bulk probe. Source error reasons are not known until that run. No reruns of accepted unaffected deeper suites.

## M04-A: three-role G2 shadow boundary attached to the existing bulk mainpath

`--bulk-deep-once` now appends `ai_shadow` from exactly one `ThreeRoleShadow` implementation. The contract verifies a current same-closed-candle G1 cohort, a source-bound local SHA256 digest, caution-free candidate preview and full trusted G1 issuer/trade coverage **before calling any provider**. Because public Indodax samples do not prove complete trade tape or issuer authentication, production M04-A must reject without calling models. SHA256 is a local payload commitment, not an authenticated provenance signature.

Unit tests inject synthetic role callers to prove MARKET → RISK → STRATEGY ordering, risk NO_TRADE/EXIT_REVIEW veto, schema/digest binding, stale-source failure and continued Final REJECT. These are NOT real paid AI tests. Sumopod provider adapter remains a separate next milestone.

On VPS: run `test_m04a_three_role_shadow.py` plus affected `test_m03i_valid_cohort.py`, then one public `--bulk-deep-once` with all trading environment flags OFF. Inspect `ai_shadow.reason` and `completed_roles` (should be empty on untrusted G1), and Final REJECT.


## M04-B: Sumopod adapter mock-ready, paid network OFF

Sumopod's official documentation confirms OpenAI-compatible Chat Completions at https://ai.sumopod.com/v1/chat/completions. The V2 adapter accepts three independently configured role model IDs through `SUMOPOD_MODEL_MARKET`, `SUMOPOD_MODEL_RISK`, and `SUMOPOD_MODEL_STRATEGY`, plus `SUMOPOD_API_KEY\` only when deliberately configured in a future release. **Do not paste/store API keys in GitHub or chats.** No model IDs or model prices are assumed current.

`SumopodChatAdapter.configured_from_env()\` still has paid-network OFF and is not created by the main CLI. A fake injected sender is available for isolated regression tests. The orchestrator has a single optional role-caller injection point for test wiring, but the normal public `--bulk-deep-once` always supplies no provider. Role input includes the same bound market microstructure snapshot and prior validated role decisions, with one local SHA digest (not issuer authentication).

Run two affected tests on VPS only: `test_m04a_three_role_shadow.py` (role input SHA changed) and `test_m04b_sumopod_adapter.py` (11 new mock adapter cases), followed by one public DRY_RUN bulk probe to confirm 0 provider roles/calls and Final REJECT. **Do not run a paid model probe or provide credentials yet.**


## M04-C: internal issuer receipt verification is not Indodax provenance

New `g1_receipt.py` validates a separate INTERNAL issuer MAC over trace, pair, candle epoch, local full market-evidence digest and expiry. The existing 3-role shadow requires this receipt before a model caller can run, regardless of Boolean trust flags. Public G1 emits no verified receipt and production does not load a key or verifier.

**Internal HMAC does not authenticate Indodax or prove complete exchange trades.** Only after a real independent G1 issuer validates actual origin, freshness, complete necessary market coverage and sound replay boundaries should a receipt be issued. This stage cannot enable real paid inference or orders.

M04-A/B synthetic unit tests were updated to use a test-only signing key; ten new M04-C cases exercise tamper, expiry, replay and wrong issuer/key rejection. Run only these three changed-scope suites on VPS before one DRY_RUN public bulk probe. No Sumopod credentials are required.

## M04-E: Official Indodax public trade stream (10/10 VPS PASS, baseline only)

Official source: https://github.com/btcid/indodax-official-api-docs/blob/master/Marketdata-websocket.md. Trade Activity channel `market:trade-activity-btcidr` carries timestamp, per-trade sequence, buy/sell, price, IDR/base amount, a publication offset and a recoverable subscription epoch. Official replay uses `recover:true` and the previously persisted offset after reconnect.

`trade_stream.py` implements one **public-only** receiver with persistent SQLite publication hashes and offsets, strict gap/epoch/replay validation and a bounded optional `websocket-client` live probe. CLI: `--public-ws-once --pair btc_idr --stream-seconds 12 --db /tmp/indodax-v2-m04e-ws.sqlite3` (all exchange trading flags OFF). Public WS static token is published in official docs, not a private account token. The Python `websocket-client` package may require installing into the **V2** environment only.

Contiguous received offsets prove only the continuity of what this client witnessed and server replayed, NOT exhaustive historical trade tape, not signed exchange-origin attestation, and certainly not buy permission. The normal 20-pair REST bulk and 3 AI shadow remains fail-closed and unchanged. Run only 10 new tests then one public WS proof; no order execution or provider call.


## M04-F: inclusive recover boundary and websocket heartbeat, no trading

After the first real public BTC stream trial (12.09sec, 0 publications, epoch1633996541, offset14642052, Final REJECT, exit0), M04-F explicitly distinguishes subscription success from real trade delivery. The official Indodax recovery example can replay the exact requested offset **inclusively**, which must be checked/skipped as a boundary rather than counted as a new trade. Newly added method 7 heartbeat responds with `{"id":3}` and diagnoses a quiet channel vs an unresponsive protocol. The bounded probe reports a persisted `DISCONNECTED` state after it closes.

New tests `tests/test_m04f_ws_heartbeat.py` (7 authored, not VPS-run) cover duplicate boundaries, replay collision, socket reconnect isolation, gaps and fake ping/pong. Run them with the affected existing M04-E suite, then two consecutive 30-second BTC/IDR PUBLIC stream probes using the SAME V2-only SQLite DB. Persisted offsets and recovery must be observed before claiming actual WS resume works. Zero frames after a 30-second session do NOT mean upstream full tape was authenticated.

No real issuer, AI paid provider, capital account, telegram owner approval or buy/sell execution is activated.


## M04-G: distinguish recovered publications from live trades (CODED, pending VPS)

At V2 SHA `513dc284`, the owner VPS proved M04-F: corrected ACK 1/1 test PASS; two public BTC/IDR 30-second WebSocket sessions (same journal) subscribed/heartbeated successfully, received 2 and 1 live publications, persisted rows 2 then 4, last offset 14642460 then 14642462, Final REJECT and exit0. The difference suggests a recovered publication during reconnect but old output did not separate it from live frames.

The same `trade_stream.py` now emits `recovered_publications_newly_persisted`, `server_replayed_publication_frames`, `live_publications_newly_persisted`, previous resume offset, ACK cursor and persisted before/after counts. Six synthetic tests are authored but NOT run on VPS. A single new 30-second PUBLIC session using `/tmp/indodax-v2-m04f-ws.sqlite3` will measure actual recovery independently. This is observed WebSocket continuity only; never G1 issuer/trade tape authorization, paid AI or order readiness.

## M04-J: staged supervised PUBLIC WebSocket collection (CODED, VPS pending)

Owner VPS at `8be8fbfb` proved M04-I: 7/7 tests PASS, three successive real BTC PUBLIC trade WebSocket sessions, 129 recovered plus 3 live publications, stored total 163, Final REJECT and exit0. This is real witnessed/replayed market-data continuity, not exhaustive trade origin authentication.

M04-J reuses the same `PublicTradeStreamJournal`, `collect_official_public_ws`, and V2 `DailyScalpingOrchestrator` in one finite locked batch, with a redacted atomic JSON last-batch status. The CLI `--public-ws-supervised-once` exits 0 only after a fully safe bounded public-data batch, and returns nonzero on gap, missing heartbeat, lock contention or any incomplete run. It never issues a G1 receipt or authorizes a paid AI model or order.

The unit template `deploy/indodax-v2-public-ws-btc.service` is committed for review only, NOT installed, started or enabled by the repository. It uses `Restart=on-success`, so a clean bounded batch can start the next one only after explicit installation/enablement by the operator, while an unsafe/nonzero failure stops the service. Its runtime DB lives inside this V2 project, not the legacy bot or transient /tmp.

Nine new tests are authored in `tests/test_m04j_ws_supervisor.py` but NOT VPS-tested. Next: nine scoped tests, then one short public-only `--public-ws-supervised-once --db /tmp/indodax-v2-m04j-pilot.sqlite3 --collector-sessions 2 --stream-seconds 10 --collector-pause-seconds 1 --supervisor-status-file /tmp/indodax-v2-m04j-pilot.status.json`. Do not install/enable systemd during this acceptance. The status sidecar is last-batch evidence only, never trading authorization.

## M04-K: read-only operator health (CODED, VPS pending)

Owner VPS at V2 SHA `1dbe301d` accepted M04-J: nine new tests PASS; real 2/2 supervised public BTC WebSocket sessions, status `PUBLIC_COLLECTION_BATCH_OK_UNATTESTED`, offset14642933, zero publications on that fresh pilot SQLite, Final REJECT and exit0. This is a finished finite batch, NOT evidence the systemd service is running or the exchange trade tape is complete.

M04-K adds `public_ws_health.py` and CLI `--public-ws-health`. It checks the **last successful public batch** status and public SQLite journal in strict read-only mode. Checks include batch freshness, exact epoch, nondecreasing SQLite publication count and offset, previous safe disconnected state, allowed status schema and tamper detection. It creates no decision journal and never calls Indodax, Sumopod or any trading API. `systemd_service_active=null` and `live_stream_currently_connected=null` remain UNKNOWN. Exit0 means only a recent internally consistent previous PUBLIC batch; exit3 means missing, stale, rolled-back or otherwise invalid evidence. Any result keeps G1/AI/execution unauthorized.

New supervised batches persist `stream_epoch` in status JSON. Old M04-J sidecar files lack that field and must not be accepted by M04-K. A new PUBLIC 2×10sec supervised batch using fresh pilot DB+sidecar is required before read-only health can PASS. Ten new tests `tests/test_m04k_ws_health.py` are authored, NOT VPS-tested. The staged systemd unit stays UNINSTALLED and UNENABLED; no user approval to enable 24/7 collection has been assumed.
