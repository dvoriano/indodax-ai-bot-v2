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

### M06-01 · Authenticated Indodax **private read-only** balance/orders/fills under explicit scoped GO.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M06-02 · Verify fresh free IDR minus locked/unsettled commitment; atomic reserve, avoid double-spend.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M06-03 · FLOKI 25% trading/75% reserve; PEPE 30% trading/70% reserve; manual holding exclusions.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M06-04 · Protect rebuy bucket proceeds and bot-owned lot/fill attribution across restarts.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

### M06-05 · Check cross-bot account balance/position ownership before LIVE coexistence.
- [ ] Kode
- [ ] Audit
- [ ] Terhubung bot
- [ ] Deploy
- [ ] Selesai

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
