# External source evidence register | V2 | 2026-11-01

## Purpose and strict scope

Owner priority (1 Nov 2026): obtain real external evidence BEFORE implementing further source-dependent trading features. Use ONLY original `dvoriano/indodax-ai-bot-v2` and official supported Indodax endpoints. No V1, Hermes, speculative price, automatic BUY, or private API without separate GO. All status data in this register is evidence metadata, not authentication or exchange proof.

## Sources verified from official documentation

1. **Official API index**: https://github.com/btcid/indodax-official-api-docs/blob/master/README.md . Its documented/supported-only statement explicitly excludes undocumented endpoints from guaranteed support.
2. **Official public REST**: https://github.com/btcid/indodax-official-api-docs/blob/master/Public-RestAPI.md . `GET /api/server_time`, `/api/pairs`, `/api/ticker_all`, `/api/trades/{pair_id}`, `/api/depth/{pair_id}` and `/tradingview/history_v2` with `tf=1,15,60`. Public REST is rate-limited to 180 requests/minute. This documented `/api/trades` endpoint lacks guaranteed paginated exhaustive 15m or 1h trade-tape range completion. 15m/1h OHLC candles are aggregates, NOT all tick-by-tick trades.
3. **Official market WS**: https://github.com/btcid/indodax-official-api-docs/blob/master/Marketdata-websocket.md . `market:trade-activity-btcidr` carries public trade rows and server `epoch/offset`. `recover=true` with a prior offset supports replay of retained publications, conditional on recoverability; docs warn some messages can be missed during disconnections. A contiguous LOCAL offset range does not demonstrate independently attested, gap-free full historical market tape.
4. **Official Trade API V2**: https://github.com/btcid/indodax-official-api-docs/blob/master/INDODAX-TradeAPI-2.md . `GET /api/v2/myTrades` is for OWN account fills only, and supports `symbol`, `startTime/endTime` at most 7 days per request, `limit` 10..1000. It is **not** a public complete market tape. Do not query without a fresh separate read-only owner GO and valid View Only V2 credentials.
5. **Official key provisioning**: https://help.indodax.com/hc/id/articles/5315217255833-Bagaimana-Cara-Mengintegrasikan-API-INDODAX ; Trade API V2 and View Only are distinct settings.

## New source-backed M07 pricing/trading constraints (October 2026)

Official current FAQ: https://help.indodax.com/hc/id/articles/4416646599705-Rincian-Biaya-Transaksi-di-INDODAX . It states the exchange adjusted its **all-in trading fee structure effective 1 October 2026**. It also states direct INDODAX Pro minimum order **Rp25,000** (orders from Rp10,000 through Rp24,999 are routed via Indodax Lite). All-in fees are **account-specific**, viewable in Profile → Settings → Trading Fees. Do **not** hardcode a stale single maker/taker fee percentage as guaranteed net PnL, and do not treat Rp10,000 target profit as a valid minimum order-size assumption. Fee schedule acceptance still requires a current explicit exchange/account source and actual applied commission on fills.

Official Trade API V2 account GET `/api/v2/account` reports `uid`, `balances[].asset/free/locked` and permission flags `canTrade/canWithdraw/canWithdrawFiat`, but merely possessing the documented schema is not proof our key is valid. Official account permissions are also documented at https://help.indodax.com/hc/id/articles/5315217255833-Bagaimana-Cara-Mengintegrasikan-API-INDODAX . Historic single GET HTTP401 cannot be treated as a successful authenticated account observation. No private call authorized here.

## Direct externally visible sample (not a full tape)

On 2026-11-01 the public `https://indodax.com/api/server_time` returned `{"timezone":"UTC","server_time":1793532895855}` (UTC 2026-11-01 11:34:55.855), observed via a read-only public web fetch. This verifies the endpoint's JSON shape and accessibility FROM THAT retrieval surface only, NOT matching local VPS clock, actual 1m/15m/1h continuity, independent issuer authentication, private account identity, or current scanner readiness. A public `/api/trades/btcidr` endpoint was externally reachable as JSON, but its row body was not captured/validated in this evidence session. Other public endpoints were not consistently retrievable from that external web tool; no fabricated samples are present.

## Original VPS BTC WebSocket measured evidence (owner proof 2026-11-01)

**Pinned source**: branch commit `341c3f526263f0a7a407af073fec95c39a8b3c12`; source-only read-only inspector `indodax_ai_bot.m04_existing_ws_evidence` executed against `/home/ubuntu/indodax-ai-bot-v2/runtime/public_ws_btc.sqlite3` through original V2 worktree. **5/5 focused offline SQLite inspector tests PASS (0.162s)** and owner marker `V2_EXISTING_PUBLIC_EVIDENCE_INSPECTED`. No newly fetched public HTTP data or new stream session, no private credentials, paid AI or trading.

- `stream_epoch=1633996541`, last recorded offset `14668280`, stream status **DISCONNECTED**.
- **16,247** persisted BTC publications, **27,171** valid locally parsed rows, **0** invalid rows and **0** local SHA-256 publication mismatches. A local hash is integrity checking, not an upstream signature.
- **5** recorded WS discontinuities, **5** jumps between stored publication offsets, **5** jumps in observed trade sequence. The latter are diagnostics only; neither timestamp nor sequence completeness is independently attested.
- First observed trade: **2026-10-27 10:52:15 WIB** (`1793073135`); last observed trade: **2026-11-01 00:01:58 WIB** (`1793466118`). The dataset is **historical**, not a live market source for the evening of November 1.
- Closed 15m UTC-aligned observed window **2026-10-31 23:45 – 2026-11-01 00:00 WIB**, `116` visible trades and `15/15` minutes with some trade data. Closed 1h window **2026-10-31 23:00 – 2026-11-01 00:00 WIB**, `696` visible trades and `60/60` minutes with some trade data. **Every minute having at least one row is NOT proof that all trades in that minute were captured.**
- `g1_authenticated=false`, `complete_public_15m_1h_tape_proven=false`, `execution_allowed=false`. Independent issuer/complete market tape remains unresolved; **M04-S/M02 G1 STRICT stays [ ]**.
- Further source-dependent engineering must use explicit read-only historical mode or a demonstrably fresh collector source; do not automatically restart the collector or enable M03 timer merely to make this evidence appear fresh.

## Required real data and acceptance by milestone

| Stage | Exact evidence required | Can collect now? | Acceptance |
| --- | --- | --- | --- |
| M02/M03 | Exchange server time, pair metadata, 1m/15m/60m closed OHLC, ticker, book, REST visible trades from coherent window, with timestamps and bounded hash | YES, public V2 original adapter with quota and bounded output; past archived 1m BTC research already exists | Source OBSERVATION only, not G1 |
| M04-S G1 | Independently authenticated upstream issuer; complete 15m AND 1h tick tape by pair; all window endpoints, distinct first/last trade, sequence/offset and recovery gaps, trade-to-OHLC, issuer-bound digest/freshness | NO complete upstream attestation documented in available official REST/WS specs. Existing WS SQLite must be examined read-only first; seek exchange/provider confirmation for exhaustive 15m/1h and upstream signed trust | NOT ACCEPTED, no source promotion |
| M04 real AI | Signed, qualified G1 source plus actual Sumopod account pricing/available model and explicit paid-network budget authorization | Documentation can be read; paid model calls need separate GO and qualifying evidence | NOT ACCEPTED |
| M05 owner | Valid M03 candidate → source-fingerprinted M04 diagnosis → real owner private Telegram inline button → persistent one-use PASS/REJECT with TTL <= 90 seconds | Existing M05 private owner bot verified in prior project history. Branch contains untested non-executable advisory outbox code; do not merge/activate until source-backed end-to-end tests | OPEN |
| M06-02..05 | Real V2 View Only account UID, balances and liabilities across requested scope, bot-owned `clientOrderId`/fills + manual pools FLOKI/PEPE history, fee/settlement | Requires new separate authorized View Only GO; prior one-GET budget already spent. Prior 401 does not substantiate balance ownership | NOT ACCEPTED |
| M07-M11 | Official exchange price increments/tradability/fee schedule, confirmed order/fill lifecycle, capital ownership, final owner GO and protective SELL reconciliation | Public metadata and API docs can be collected first, actual private trading requires explicit separate owner GO | NOT ACCEPTED |

## Work precedence

1. Inspect EXISTING `runtime/public_ws_btc.sqlite3` using a **read-only SQLite transaction**. Record current stream state, distinct epochs, publication offset gaps, discontinuity count, first/last trade timestamp and observed 15m/1h spans. Do not claim completeness or add a new WS collector.
2. Obtain actual bounded public REST data with original `IndodaxPublicMarket` shared `SharedPublicRestBudget`, not ad hoc unaccounted HTTP probes. Compare UTC timestamps and align closed 1m/15m/60m windows. A failed source remains diagnostic.
3. Check official external provider/Indodax capability for supported exhaustive historical market tape and independently verifiable source statement. If unavailable, label G1 STRICT upstream-dependent and keep source trust false.
4. In parallel, continue safe engineering of existing M05 owner advisory and M07 hardlocked interfaces, but never mark end-to-end qualified AI or LIVE trading accepted until actual evidence exists.
5. M06 next private data session is separately scoped with valid View Only V2 key, a NEW owner-approved budget and consent. Never reuse spent GO or display secrets.

## Open-checklist reconciliation

Current `docs/ROADMAP.md` has 48 open `- [ ]` items across M02–M12. This evidence register does NOT close any item, does NOT change 50 done/48 open, and does NOT convert source observations into proof of G1, M06 ownership, executor readiness or Telegram LIVE.
