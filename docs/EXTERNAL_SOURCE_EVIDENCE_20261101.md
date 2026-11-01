# External source evidence register | V2 | 2026-11-01

## Purpose and strict scope

Owner priority (1 Nov 2026): obtain real external evidence BEFORE implementing further source-dependent trading features. Use ONLY original `dvoriano/indodax-ai-bot-v2` and official supported Indodax endpoints. No V1, Hermes, speculative price, automatic BUY, or private API without separate GO. All status data in this register is evidence metadata, not authentication or exchange proof.

## Sources verified from official documentation

1. **Official API index**: https://github.com/btcid/indodax-official-api-docs/blob/master/README.md . Its documented/supported-only statement explicitly excludes undocumented endpoints from guaranteed support.
2. **Official public REST**: https://github.com/btcid/indodax-official-api-docs/blob/master/Public-RestAPI.md . `GET /api/server_time`, `/api/pairs`, `/api/ticker_all`, `/api/trades/{pair_id}`, `/api/depth/{pair_id}` and `/tradingview/history_v2` with `tf=1,15,60`. Public REST is rate-limited to 180 requests/minute. This documented `/api/trades` endpoint lacks guaranteed paginated exhaustive 15m or 1h trade-tape range completion. 15m/1h OHLC candles are aggregates, NOT all tick-by-tick trades.
3. **Official market WS**: https://github.com/btcid/indodax-official-api-docs/blob/master/Marketdata-websocket.md . `market:trade-activity-btcidr` carries public trade rows and server `epoch/offset`. `recover=true` with a prior offset supports replay of retained publications, conditional on recoverability; docs warn some messages can be missed during disconnections. A contiguous LOCAL offset range does not demonstrate independently attested, gap-free full historical market tape.
4. **Official Trade API V2**: https://github.com/btcid/indodax-official-api-docs/blob/master/INDODAX-TradeAPI-2.md . `GET /api/v2/myTrades` is for OWN account fills only, and supports `symbol`, `startTime/endTime` at most 7 days per request, `limit` 10..1000. It is **not** a public complete market tape. Do not query without a fresh separate read-only owner GO and valid View Only V2 credentials.
5. **Official key provisioning**: https://help.indodax.com/hc/id/articles/5315217255833-Bagaimana-Cara-Mengintegrasikan-API-INDODAX ; Trade API V2 and View Only are distinct settings.

## Direct externally visible sample (not a full tape)

On 2026-11-01 the public `https://indodax.com/api/server_time` returned `{"timezone":"UTC","server_time":1793532895855}` (UTC 2026-11-01 11:34:55.855), observed via a read-only public web fetch. This verifies the endpoint's JSON shape and accessibility FROM THAT retrieval surface only, NOT matching local VPS clock, actual 1m/15m/1h continuity, independent issuer authentication, private account identity, or current scanner readiness. A public `/api/trades/btcidr` endpoint was externally reachable as JSON, but its row body was not captured/validated in this evidence session. Other public endpoints were not consistently retrievable from that external web tool; no fabricated samples are present.

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
