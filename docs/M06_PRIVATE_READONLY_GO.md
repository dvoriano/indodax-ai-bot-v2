# M06 Private Read-only GO (owner approval, 31 October 2026)

This is a **security runbook**, not a second roadmap. Acceptance and five gates remain in `docs/ROADMAP.md` and `docs/OPEN_CHECKLIST_STAGES.md`.

## Explicit consent and limits

Owner authorized **M06_PRIVATE_READONLY_GO** for one Indodax V2 account, four GET endpoints only: `/api/v2/account`, `/api/v2/openOrders`, `/api/v2/order/histories`, `/api/v2/myTrades`. Maximum **15 minutes and 20 attempted requests total** for the approved window. BUY, SELL, CANCEL, withdrawal, other account requests, paid AI and LIVE are **NOT** approved. This consent does not automatically install or start a service, create credentials, or extend itself into a new future window. Obtain a **fresh, explicit approval** if offline verification/review outlasts the original 15-minute window.

## Implementation and safety boundaries

- Caller: original V2 CLI `--m06-private-readonly-once` → `DailyScalpingOrchestrator` → `m06_scoped_https.run_scoped_private_probe`. It never calls an executor.
- Requires `INDODAX_TRADING_ENABLED=NO`, `INDODAX_ALLOW_LIVE_EXECUTION=NO`, `INDODAX_EXECUTION_MODE=DRY_RUN`. Do not enable trading to make this probe run.
- Default run requires **two operator-created, owner-only 0600 JSON files** on VPS, neither stored in the repository:
  - GO file with EXACT keys: `approval_id`, `issued_ms`, `expires_ms`, `max_requests`, `symbol`; `approval_id` is `M06_PRIVATE_READONLY_GO_20261031`, `max_requests` at most 20, `expires_ms-issued_ms` at most 900000 ms, `symbol` one lower-case IDR pair such as `injidr`. Timestamps are epoch **milliseconds**. Invalid, expired, or future approvals fail closed.
  - Credentials file with EXACT `api_key` and `secret_key`. Create a **separate Indodax Trade API V2 key with Reading (View Only) only**. Never copy a trading or withdrawal-enabled key. Do not post these secrets in GitHub, terminal logs, or chat.
- Fixed durable budget path: `~/.local/state/indodax-ai-bot-v2/m06-readonly-go-20261031.sqlite3`. Its existing containing directory must be owned by the VPS user and have **0700** permissions. DB file is created 0600. Same GO record cannot be renewed or replaced; an attempted HTTPS GET is debited **before sending** and persists across crashes/restarts. Never delete/recreate the DB to recover quota.
- The HTTPS client pins `api.indodax.com:443`, verifies server TLS, signs exact HMAC-SHA256 query, sends GET only, disallows redirects and proxy use, times out after 5s and caps JSON to 1MB. No retries; any error is a REJECT with no response body or secret printed.
- Account permissions `canTrade`, `canWithdraw`, `canWithdrawFiat` must ALL be JSON **false**. Otherwise stop after first account GET. Missing flags are a rejection.
- `openOrders` is requested without pair filter to avoid hiding other pair commitments. An unknown quote asset or 1000-row response blocks. Histories/fills are only for the approved symbol and may be incomplete; **never promote to whole-account order/fill completeness, ownership, spendability or LIVE**.
- Operator output contains limited counts/status only. Do not publish raw account UID, balances, orders, fills, credentials, signed headers, or full HTTP responses.

## Rekonsiliasi ID dan batas cakupan M06 (31 Oktober 2026)

Rujukan: dokumentasi resmi Indodax Trade API V2 pada `INDODAX-TradeAPI-2.md`. `GET /api/v2/openOrders` mengembalikan `orderId` numerik serta `fullOrderId` berbentuk `symbol-type-id`. Sebaliknya, `GET /api/v2/order/histories` dan `GET /api/v2/myTrades` menggunakan **full order ID**. Rekonsiliasi harus join `openOrders.fullOrderId == history.orderId == myTrades.orderId`, bukan menyamakan ID numerik dengan ID lengkap atau mengandalkan awalan nama order. `clientOrderId`, `isBuyer`, serta kuantitas fill harus konsisten bila tersedia. ID yang hilang atau konflik menghasilkan REJECT; pengujian fixture tidak pernah menerbitkan `bot_owned_verified`.

**Cakupan histori:** order histories dan myTrades secara default hanya menampilkan **24 jam terakhir**, dengan batas maksimum window permintaan 7 hari. Satu GET empat endpoint, sekalipun berhasil, tidak membuktikan histori lengkap, modal terlindungi dari seluruh order akun, atau pemisahan bot lain. Keempat endpoint M06 hanya melayani diagnostik read-only; `history_complete=False`, `bot_owned_fills_verified=False`, `cross_bot_ownership_verified=False`, dan `execution_allowed=False` tetap. Untuk closure M06-02 sampai M06-05 dibutuhkan penerimaan end-to-end lain dengan riwayat lintas-window, ownership account dan protected rebuy yang benar-benar teruji.

**GO lama tidak dapat diperpanjang lewat percakapan atau file lokal.** Persetujuan awal maksimal 15 menit/20 request adalah jendela sekali pakai, bukan otorisasi permanen; apabila window terlewat, perlu izin baru yang eksplisit sebelum request privat apa pun. Tidak boleh membuat waktu awal baru sendiri lalu menyebutnya kelanjutan GO 31 Oktober.

**Hardlock pada PR#29 sekarang:** `m06_scoped_https._NETWORK_RELEASE_GO = False`. Bahkan request GET yang berhasil ditandatangani dan file izin lokal yang valid tidak akan mencapai socket HTTPS. Perubahan ini disengaja karena GO 15 menit yang diberikan sebelumnya sudah kedaluwarsa. Jangan mengubah flag menjadi `True` melalui VPS lokal atau pull request tanpa GO baru, verifikasi paket offline, dan keputusan rilis SHA yang eksplisit. Semua tes offline memakai mock transport sehingga hardlock tetap berlaku.

## Protected rebuy lintas-restart, offline-only

`M06OwnershipLedger` yang **sama** kini menambah dua tabel di SQLite V2 (bukan engine/domain baru): `m06_special_rebuy_observations` untuk hasil **gross IDR** dari fill SELL `flokiidr` / `pepeidr` yang cocok dengan intent lokal, serta `m06_manual_asset_floors` untuk batas minimum kuantitas aset manual. Setiap `trade_id` unik, diikat hash akun/order, dan seluruh perubahan memakai transaksi `BEGIN IMMEDIATE`. Repeated observation yang identik tidak menduplikasi cadangan; record yang berubah, fill foreign atau jumlah fill melebihi snapshot ditolak secara atomik. Rebuy BUY yang baru teramati **tidak** otomatis mengurangi dana protected; hanya pengurangan setelah bukti settlement exchange dan acceptance terpisah yang kelak boleh dirancang. Manual floor hanya naik/tetap, tidak boleh turun akibat restart atau input lokal.

`DailyScalpingOrchestrator.review_m06_local_intent_ownership` tetap caller asli, kini menghasilkan protected rebuy review; `review_m06_protected_rebuy_offline`, `protect_m06_manual_holding_floor_offline` dan `reconcile_m06_capital_with_local_rebuy_offline` menggunakan **database ownership yang sama**. Reconciliation dari ledger lokal sengaja selalu `all_pairs_open_orders_complete=False`, `calculated_available_idr=0`, dan `execution_allowed=False`. Nominal lokal bukan bukti IDR yang sudah settled atau seluruh FLOKI/PEPE dimiliki bot, dan bukan basis izin BUY ataupun SELL. Manual floor bukan bukti exchange ownership. M06-04 dan M06-05 tetap OPEN sampai actual authenticated fills, complete history, cross-bot ownership dan installed runtime diverifikasi.

## UID-bound local evidence

Original M06 private diagnostic now reads the existing V2 journal SQLite as a read-only local source. The lookup uses the account UID returned by the account parser; it does not accept an arbitrary user account label. The file must be private and already exist. Missing or incompatible local evidence is reported as unavailable, without creating new tables.

This is **CODED only** until the new tests pass on pinned V2 source. A matching local ledger is not verified exchange ownership. All trade and capital permissions remain OFF. Historical data from another account or unbound local records must not be silently migrated.

## Acceptance before first private request

1. Inspect the exact Git SHA in PR #29 and run syntax checks plus targeted **fully offline mocked** tests for GO, key veto, quota, original CLI → orchestrator wiring and zero-network no-GO.
2. Verify deployment of the exact accepted SHA to the one V2 runtime, with READ-ONLY settings and unchanged trade service boundaries. A detached worktree test is not deployment.
3. Confirm the Indodax key is view-only in the exchange UI. Create both secure local JSON files, the private state directory, and new GO time window **only with current owner approval**; never paste credentials into this conversation.
4. Run a single foreground bounded CLI probe; verify summary and SQLite consumed count. No scheduled timer, Telegram invocation or retries. If any scope gate fails, stop; do not broaden consent.

Until the above gates are evidenced, **no live private account request** and no checklist marked complete. Even a successful four-GET probe does not establish cross-bot ownership or historic fill completeness.
