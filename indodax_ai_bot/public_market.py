"""Official Indodax public REST data only. No authentication or trading endpoints.

Docs: https://github.com/btcid/indodax-official-api-docs/blob/master/Public-RestAPI.md
Only supported 1m and 15m OHLC endpoints requested; 5m is aggregated from 1m.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
import json
import os
import re
from time import time
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .contracts import MarketSnapshot, SourceReceipt
from .public_rest_budget import SharedPublicRestBudget, SharedRestBudgetError
from .m03_egress_admission import M03SharedEgressAdmission, M03EgressAdmissionError


class PublicDataError(ValueError):
    """Public payload invalid, unavailable or too old."""


_PAIR_RE = re.compile(r"^[a-z0-9]+_idr$")


def _number(value: Any, label: str, *, zero: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise PublicDataError(f"INVALID_{label}")
    try:
        d = Decimal(str(value))
    except InvalidOperation as exc:
        raise PublicDataError(f"INVALID_{label}") from exc
    if not d.is_finite() or (d < 0 if zero else d <= 0):
        raise PublicDataError(f"INVALID_{label}")
    return d


def _seconds(v: Any, label: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise PublicDataError(f"INVALID_{label}")
    try:
        n = int(v)
    except ValueError as exc:
        raise PublicDataError(f"INVALID_{label}") from exc
    if n <= 0:
        raise PublicDataError(f"INVALID_{label}")
    # Indodax /api/server_time uses milliseconds; ticker/trades use seconds.
    return n // 1000 if n > 100_000_000_000 else n


@dataclass(frozen=True)
class Candle:
    start: int
    interval: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class PublicScanContext:
    """Shared PUBLIC-only discovery receipt, current cycle only. Not signed provenance."""
    observed_epoch: int
    exchange_epoch: int
    pairs: dict[str, tuple[str, str]]
    tickers: dict[str, Any]
    # Public research only: status is NOT independent proof a pair is tradable.
    maintenance_pair_ids: frozenset[str] = frozenset()
    # Exact fields from SAME official /api/pairs batch, no extra request.
    # Missing fields remain UNKNOWN, never active/executable by default.
    pair_rule_fields: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class PublicObservation:
    pair: str
    snapshot: MarketSnapshot
    candle_1m: Candle
    candle_5m: Candle
    candle_15m: Candle
    trade_count_visible: int
    trade_history_complete: bool
    depth_levels_visible: int
    ready: bool
    reason: str
    # These are source-derived facts, not fabricated completed trade coverage.
    candles_1m: tuple[Candle, ...] = ()
    candles_15m: tuple[Candle, ...] = ()
    visible_closed_1m_buy_idr: Decimal = Decimal("0")
    visible_closed_1m_sell_idr: Decimal = Decimal("0")
    visible_closed_1m_trade_count: int = 0
    bid_depth_top5_idr: Decimal = Decimal("0")
    ask_depth_top5_idr: Decimal = Decimal("0")
    visible_closed_1m_buy_count: int = 0
    visible_closed_1m_sell_count: int = 0
    # Price/asset-quantity ladders from the SAME REST depth snapshot.
    # First 10 per side are not exchange-attested and may be canceled.
    bid_book_levels: tuple[tuple[Decimal, Decimal], ...] = ()
    ask_book_levels: tuple[tuple[Decimal, Decimal], ...] = ()
    pair_rule_fields: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, str | int | bool]:
        return {
            "pair": self.pair, "source": self.snapshot.source.provenance,
            "closed_1m_epoch": self.snapshot.source.closed_1m_epoch,
            "observed_epoch": self.snapshot.source.observed_epoch,
            "g1_issuer_verified": self.snapshot.source.verified,
            "market_data_ready": self.ready, "market_data_reason": self.reason,
            "best_bid_idr": str(self.snapshot.best_bid_idr),
            "best_ask_idr": str(self.snapshot.best_ask_idr),
            "candle_5m_start": self.candle_5m.start,
            "candle_15m_start": self.candle_15m.start,
            "trades_visible": self.trade_count_visible,
            "visible_closed_1m_buy_count_sampled": self.visible_closed_1m_buy_count,
            "visible_closed_1m_sell_count_sampled": self.visible_closed_1m_sell_count,
            "visible_trade_side_counts_complete": False,
            "book_ladder_levels_bid_visible": len(self.bid_book_levels),
            "book_ladder_levels_ask_visible": len(self.ask_book_levels),
            "book_liquidity_is_guaranteed": False,
            "trade_history_complete": self.trade_history_complete,
            "depth_levels_visible": self.depth_levels_visible,
            "public_pair_rule_fields_present": bool(self.pair_rule_fields),
            "public_pair_tradability_verified": False,
        }


@dataclass(frozen=True)
class PublicCandleHistory:
    """Contiguous official PUBLIC OHLC rows, not attested trade history."""
    pair: str
    observed_epoch: int
    exchange_epoch: int
    from_epoch: int
    to_epoch: int
    candles: tuple[Candle, ...]
    provenance: str = "INDODAX_PUBLIC_OHLC_V2_UNATTESTED"
    source_trust_verified: bool = False
    trade_history_complete: bool = False


class IndodaxPublicMarket:
    """Bounded public-only HTTP adapter: NEVER sends credentials/private requests."""

    ROOT = "https://indodax.com"

    def __init__(self, *, fetch: Callable[[str], Any] | None = None,
                 clock: Callable[[], float] = time, timeout: float = 8.0,
                 rest_budget: SharedPublicRestBudget | None = None,
                 m03_light_only_public_watch: bool = False) -> None:
        if type(m03_light_only_public_watch) is not bool:
            raise PublicDataError("M03_LIGHT_PUBLIC_SCOPE_INVALID")
        self._m03_light_only_public_watch = m03_light_only_public_watch
        self._clock = clock
        self._timeout = timeout
        self._fetch = fetch or self._http_json
        # Live public HTTP ALWAYS shares a local-host, cross-process quota.
        # Explicit synthetic fetch skips it unless a test injects a guard.
        self._rest_budget = (rest_budget if rest_budget is not None else
                             SharedPublicRestBudget() if fetch is None else None)
        # Opt-in only for original M03 scheduled caller. Missing policy or
        # unknown peer demand blocks BEFORE every real HTTP attempt.
        mode = os.environ.get("INDODAX_M03_REQUIRE_EGRESS_ADMISSION", "NO")
        if mode not in ("NO", "YES"):
            raise PublicDataError("M03_EGRESS_ADMISSION_MODE_INVALID")
        self._m03_admission = M03SharedEgressAdmission() if mode == "YES" else None
        # Per-client diagnostic only: counts attempted PUBLIC GETs in this
        # instance. No global IP accounting; no authorization impact.
        self.public_get_attempt_count = 0
        # Captured only from the same /api/pairs snapshot, not persisted.
        self.public_maintenance_pairs: frozenset[str] = frozenset()
        self.public_pair_rule_fields: dict[str, dict[str, Any]] = {}

    def _http_json(self, path: str) -> Any:
        # Every path comes from hardcoded public endpoint constructors.
        url = self.ROOT + path
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "indodax-ai-bot-v2-public/0.2"})
        try:
            with urlopen(request, timeout=self._timeout) as response:
                payload = response.read(2_000_001)
                if len(payload) > 2_000_000:
                    raise PublicDataError("PUBLIC_RESPONSE_TOO_LARGE")
            return json.loads(payload)
        except HTTPError as exc:
            # Report only fixed sanitized codes to the original scheduled
            # scanner. Preserve typed HTTPError as the explicit cause for
            # the EXISTING owner-invoked M03 Cloudflare evidence collector,
            # which separately allowlists status, CF-Ray and content type.
            # Never include response bodies or arbitrary headers in reasons,
            # retry forbidden endpoints, rotate egress or promote G1/orders.
            if exc.code == 403:
                raise PublicDataError("PUBLIC_REST_HTTP_403_FORBIDDEN") from exc
            if exc.code == 429:
                raise PublicDataError("PUBLIC_REST_HTTP_429_RATE_LIMITED") from exc
            if exc.code in (502, 503, 504):
                raise PublicDataError("PUBLIC_REST_HTTP_5XX_UNAVAILABLE") from exc
            raise PublicDataError("PUBLIC_HTTP_OR_JSON_UNAVAILABLE") from exc
        except (OSError, ValueError) as exc:
            raise PublicDataError("PUBLIC_HTTP_OR_JSON_UNAVAILABLE") from exc

    def _get(self, path: str) -> Any:
        self.public_get_attempt_count += 1
        try:
            allowed = (self._m03_admission.allowed_local_limit(
                require_btc_source=not self._m03_light_only_public_watch,
            ) if self._m03_admission is not None else None)
        except M03EgressAdmissionError as exc:
            raise PublicDataError(str(exc)) from None
        if self._rest_budget is not None:
            try:
                self._rest_budget.reserve(limit_override=allowed)
            except SharedRestBudgetError as exc:
                raise PublicDataError(str(exc)) from None
        elif allowed is not None:
            # Admitted M03 never skips the original atomic quota, even
            # when given an injected alternate transport.
            raise PublicDataError("M03_EGRESS_LOCAL_BUDGET_REQUIRED")
        try:
            return self._fetch(path)
        except PublicDataError:
            raise
        except (OSError, ValueError, TypeError, TimeoutError) as exc:
            raise PublicDataError("PUBLIC_REQUEST_FAILED") from exc

    def idr_pairs(self) -> dict[str, tuple[str, str]]:
        data = self._get("/api/pairs")
        if not isinstance(data, list):
            raise PublicDataError("INVALID_PAIRS_PAYLOAD")
        pairs: dict[str, tuple[str, str]] = {}
        maintenance: set[str] = set()
        rules: dict[str, dict[str, Any]] = {}
        for item in data:
            if not isinstance(item, dict) or item.get("base_currency") != "idr":
                continue
            ticker_id, pair_id, symbol = (item.get(k) for k in ("ticker_id", "id", "symbol"))
            if not all(isinstance(s, str) for s in (ticker_id, pair_id, symbol)):
                continue
            if not _PAIR_RE.fullmatch(ticker_id) or pair_id != ticker_id.replace("_", ""):
                continue
            # Exchange-provided TradingView symbol may be a distinct alias,
            # e.g. ticker ACTSOL_IDR / id actsolidr / symbol ACTIDR.
            # Bound it to the SAME validated pair id and IDR quote, never
            # synthesize a symbol or accept a foreign quote.
            if not re.fullmatch(r"[A-Z0-9]+IDR", symbol.upper()):
                continue
            traded = item.get("traded_currency")
            if traded is not None and traded != ticker_id[:-4]:
                continue
            # Only is_market_suspended is an explicit market suspension gate.
            # Missing flag is legacy PUBLIC-only research compatibility, NOT
            # executable trading status. Unknown values reject admission.
            if item.get("is_market_suspended") not in (None, 0, False, "0"):
                continue
            m = item.get("is_maintenance")
            if m not in (None, 0, 1, False, True, "0", "1"):
                continue
            # Indodax wallet maintenance is not necessarily a halt of trades.
            # Capture a CAUTION but allow public radar/candles only. Execution
            # remains prohibited until future independent M07 tradability check.
            if m in (1, True, "1"):
                maintenance.add(ticker_id)
            pairs[ticker_id] = (pair_id, symbol.upper())
            # Preserve exact optional public order constraints as observed.
            # No absent value is filled, and no published fee is assumed to
            # validate per-pair precision or live order tradability.
            allowed = (
                "price_precision", "quantity_increment",
                "trade_min_base_currency", "trade_min_traded_currency",
                "is_market_suspended", "is_maintenance",
            )
            rules[ticker_id] = {key: item[key] for key in allowed if key in item}
        self.public_maintenance_pairs = frozenset(maintenance)
        self.public_pair_rule_fields = rules
        if not pairs:
            raise PublicDataError("NO_ACTIVE_IDR_PAIRS")
        return pairs

    @staticmethod
    def _candles(payload: Any, interval: int, now: int) -> list[Candle]:
        if not isinstance(payload, list):
            raise PublicDataError("INVALID_OHLC_PAYLOAD")
        out: dict[int, Candle] = {}
        for row in payload:
            if not isinstance(row, dict):
                raise PublicDataError("INVALID_OHLC_ROW")
            start = _seconds(row.get("Time"), "CANDLE_TIME")
            if start % interval or start > now + 3:
                raise PublicDataError("INVALID_CANDLE_TIME")
            o, h, l, c = (_number(row.get(k), k.upper()) for k in ("Open", "High", "Low", "Close"))
            v = _number(row.get("Volume"), "VOLUME", zero=True)
            if not l <= min(o, c) <= max(o, c) <= h:
                raise PublicDataError("INVALID_OHLC_RANGE")
            if start in out:
                raise PublicDataError("DUPLICATE_CANDLE")
            # The currently forming candle MUST NOT be used.
            if start + interval <= now:
                out[start] = Candle(start, interval, o, h, l, c, v)
        return sorted(out.values(), key=lambda x: x.start)

    @staticmethod
    def _aggregate_5m(candles: list[Candle], now: int) -> Candle:
        if not candles:
            raise PublicDataError("MISSING_1M_CANDLE")
        # Current closing 5m bucket may be incomplete. Use a real full closed bucket.
        buckets = sorted({c.start - c.start % 300 for c in candles if c.start + 60 <= now}, reverse=True)
        by_time = {c.start: c for c in candles}
        for start in buckets:
            if start + 300 > now:
                continue
            expected = [by_time.get(start + i * 60) for i in range(5)]
            if all(c is not None for c in expected):
                batch = [c for c in expected if c is not None]
                return Candle(start, 300, batch[0].open, max(c.high for c in batch),
                              min(c.low for c in batch), batch[-1].close, sum((c.volume for c in batch), Decimal(0)))
        raise PublicDataError("INCOMPLETE_5M_FROM_1M")

    def observe(self, pair: str = "btc_idr", *,
                scan_context: PublicScanContext | None = None) -> PublicObservation:
        if not _PAIR_RE.fullmatch(pair):
            raise PublicDataError("INVALID_IDR_PAIR")
        if scan_context is None:
            pairs = self.idr_pairs()
            observed = int(self._clock())
        else:
            observed = int(self._clock())
            if not isinstance(scan_context, PublicScanContext):
                raise PublicDataError("INVALID_PUBLIC_SCAN_CONTEXT")
            if (observed < scan_context.observed_epoch
                    or observed - scan_context.observed_epoch > 30):
                raise PublicDataError("EXPIRED_PUBLIC_SCAN_CONTEXT")
            pairs = scan_context.pairs
        if pair not in pairs:
            raise PublicDataError("PAIR_NOT_ACTIVE")
        pair_id, symbol = pairs[pair]
        if scan_context is None:
            server_data = self._get("/api/server_time")
            if not isinstance(server_data, dict):
                raise PublicDataError("INVALID_SERVER_TIME")
            exchange_now = _seconds(server_data.get("server_time"), "SERVER_TIME")
        else:
            exchange_now = scan_context.exchange_epoch
        if abs(exchange_now - observed) > 30:
            raise PublicDataError("EXCHANGE_CLOCK_SKEW")
        if scan_context is None:
            ticker_response = self._get(f"/api/ticker/{pair_id}")
            ticker = ticker_response.get("ticker") if isinstance(ticker_response, dict) else None
        else:
            # Reuse the SAME ticker_all payload already inspected by scanner.
            # Never infer a ticker value from a different observation minute.
            ticker = scan_context.tickers.get(pair)
        if not isinstance(ticker, dict):
            raise PublicDataError("INVALID_TICKER")
        ticker_sec = _seconds(ticker.get("server_time"), "TICKER_TIME")
        if ticker_sec > observed + 3 or observed - ticker_sec > 60:
            raise PublicDataError("STALE_TICKER")
        bid = _number(ticker.get("buy"), "BID")
        ask = _number(ticker.get("sell"), "ASK")
        if ask < bid:
            raise PublicDataError("CROSSED_TICKER")
        history_from = observed - 35 * 60
        params = lambda tf: urlencode({"from": history_from, "to": observed, "symbol": symbol, "tf": str(tf)})
        one = self._candles(self._get("/tradingview/history_v2?" + params(1)), 60, observed)
        fifteen = self._candles(self._get("/tradingview/history_v2?" + params(15)), 900, observed)
        if not one or not fifteen:
            raise PublicDataError("MISSING_CLOSED_MULTITIMEFRAME")
        latest_1 = one[-1]
        if observed - (latest_1.start + 60) > 120:
            raise PublicDataError("STALE_CLOSED_1M")
        five = self._aggregate_5m(one, observed)
        if latest_1.start - (five.start + 300) > 300:
            raise PublicDataError("STALE_5M")
        latest_15 = fifteen[-1]
        if latest_1.start - (latest_15.start + 900) > 900:
            raise PublicDataError("STALE_15M")
        trades = self._get(f"/api/trades/{pair_id}")
        if not isinstance(trades, list):
            raise PublicDataError("INVALID_TRADES")
        buy_flow = Decimal("0")
        sell_flow = Decimal("0")
        visible_trade_count_closed = 0
        visible_buy_count = 0
        visible_sell_count = 0
        for trade in trades:
            if not isinstance(trade, dict):
                raise PublicDataError("INVALID_TRADE_ROW")
            ts = _seconds(trade.get("date"), "TRADE_TIME")
            if ts > observed + 3:
                raise PublicDataError("FUTURE_TRADE")
            price = _number(trade.get("price"), "TRADE_PRICE")
            amount = _number(trade.get("amount"), "TRADE_AMOUNT")
            if trade.get("type") not in ("buy", "sell"):
                raise PublicDataError("INVALID_TRADE_SIDE")
            # The public response is capped/page-limited. This is a VISIBLE
            # sample only, never a claim of complete 1m trade coverage.
            if latest_1.start <= ts < latest_1.start + 60:
                visible_trade_count_closed += 1
                if trade["type"] == "buy":
                    buy_flow += price * amount
                    visible_buy_count += 1
                else:
                    sell_flow += price * amount
                    visible_sell_count += 1
        depth = self._get(f"/api/depth/{pair_id}")
        if not isinstance(depth, dict) or not isinstance(depth.get("buy"), list) or not isinstance(depth.get("sell"), list):
            raise PublicDataError("INVALID_DEPTH")
        ladders: dict[str, tuple[tuple[Decimal, Decimal], ...]] = {}
        for side in ("buy", "sell"):
            validated = []
            for level in depth[side]:
                if not isinstance(level, list) or len(level) < 2:
                    raise PublicDataError("INVALID_DEPTH_LEVEL")
                validated.append((
                    _number(level[0], "DEPTH_PRICE"),
                    _number(level[1], "DEPTH_AMOUNT"),
                ))
            # Bound evidence footprint; do not invent missing depth levels.
            # An unordered REST response is unusable for a distance-based wall.
            prices = [row[0] for row in validated]
            if any(
                (a <= b if side == "buy" else a >= b)
                for a, b in zip(prices, prices[1:])
            ):
                raise PublicDataError("UNSORTED_OR_DUPLICATE_BOOK_LEVEL")
            ladders[side] = tuple(validated[:10])
        if not depth["buy"] or not depth["sell"]:
            raise PublicDataError("EMPTY_DEPTH")
        if _number(depth["buy"][0][0], "DEPTH_BID") > _number(depth["sell"][0][0], "DEPTH_ASK"):
            raise PublicDataError("CROSSED_DEPTH")
        source = SourceReceipt(pair, latest_1.start + 60, observed, "INDODAX_PUBLIC_OHLC_V2_UNATTESTED", False)
        snapshot = MarketSnapshot(source, bid, ask)
        # Public REST trades are page-limited; a sampled history is not a complete 1h ledger.
        bid_depth5 = sum((_number(x[0], "DEPTH_PRICE") * _number(x[1], "DEPTH_AMOUNT")
                          for x in depth["buy"][:5]), Decimal("0"))
        ask_depth5 = sum((_number(x[0], "DEPTH_PRICE") * _number(x[1], "DEPTH_AMOUNT")
                          for x in depth["sell"][:5]), Decimal("0"))
        return PublicObservation(
            pair, snapshot, latest_1, five, latest_15,
            len(trades), False, len(depth["buy"]) + len(depth["sell"]),
            False, "G1_PROVENANCE_AND_TRADE_COVERAGE_UNVERIFIED",
            tuple(one), tuple(fifteen),
            buy_flow, sell_flow, visible_trade_count_closed,
            bid_depth5, ask_depth5,
            visible_buy_count, visible_sell_count,
            ladders["buy"], ladders["sell"],
            dict((scan_context.pair_rule_fields if scan_context is not None
                  else self.public_pair_rule_fields).get(pair, {})),
        )

    def closed_1m_research_history(self, pair: str = "btc_idr", *,
                                   minutes: int = 180,
                                   end_epoch: int | None = None) -> PublicCandleHistory:
        """M03-K: bounded PUBLIC OHLC research, not a source-trust upgrade.

        Three official GETs: pairs, server_time, tradingview 1m. Missing
        1m bars reject the entire window; never fabricate empty bars.
        """
        if not isinstance(pair, str) or not _PAIR_RE.fullmatch(pair):
            raise PublicDataError("INVALID_IDR_PAIR")
        if type(minutes) is not int or not 90 <= minutes <= 360:
            raise PublicDataError("INVALID_RESEARCH_HISTORY_MINUTES")
        pairs = self.idr_pairs()
        if pair not in pairs:
            raise PublicDataError("PAIR_NOT_ACTIVE")
        observed_epoch = int(self._clock())
        response = self._get("/api/server_time")
        if not isinstance(response, dict):
            raise PublicDataError("INVALID_SERVER_TIME")
        exchange_epoch = _seconds(response.get("server_time"), "SERVER_TIME")
        if abs(exchange_epoch - observed_epoch) > 30:
            raise PublicDataError("EXCHANGE_CLOCK_SKEW")
        latest_closed_epoch = (min(observed_epoch, exchange_epoch) // 60) * 60
        # Historical backfill is explicit and bound to an already closed
        # minute. Never allow a future or partially formed candle.
        if end_epoch is not None:
            if (type(end_epoch) is not int or end_epoch <= 0
                    or end_epoch % 60 != 0
                    or end_epoch > latest_closed_epoch):
                raise PublicDataError("INVALID_RESEARCH_HISTORY_END")
            to_epoch = end_epoch
        else:
            to_epoch = latest_closed_epoch
        from_epoch = to_epoch - minutes * 60
        if from_epoch <= 0:
            raise PublicDataError("INVALID_RESEARCH_HISTORY_RANGE")
        symbol = pairs[pair][1]
        path = "/tradingview/history_v2?" + urlencode({
            "from": from_epoch, "to": to_epoch,
            "symbol": symbol, "tf": "1",
        })
        rows = self._candles(self._get(path), 60, to_epoch)
        rows = [x for x in rows
                if from_epoch <= x.start and x.start + 60 <= to_epoch]
        if (len(rows) != minutes or rows[0].start != from_epoch
                or rows[-1].start + 60 != to_epoch
                or any(b.start - a.start != 60
                       for a, b in zip(rows, rows[1:]))):
            raise PublicDataError("RESEARCH_HISTORY_1M_INCOMPLETE")
        return PublicCandleHistory(
            pair=pair, observed_epoch=observed_epoch,
            exchange_epoch=exchange_epoch, from_epoch=from_epoch,
            to_epoch=to_epoch, candles=tuple(rows),
        )


class IndodaxPublicV2RecentTradesResearch(IndodaxPublicMarket):
    """Owner-invoked diagnostic only; shares the original REST quota.

    Official TAPI v2 public recent trades endpoint, not a fallback for
    blocked legacy requests. No authentication, retries, paging or orders.
    """
    ROOT = "https://api.indodax.com"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Real diagnostic transport must honor the existing owner egress
        # admission, even when the interactive shell lacks M03 flags.
        if kwargs.get("fetch") is None:
            if self._m03_admission is None:
                self._m03_admission = M03SharedEgressAdmission()
            self._m03_light_only_public_watch = False

    def recent_trades_sample(self, pair: str) -> Any:
        if type(pair) is not str or not _PAIR_RE.fullmatch(pair) or len(pair)>40:
            raise PublicDataError("INVALID_IDR_PAIR")
        from .scanner import MAIN_EXCLUDED_PAIRS
        if pair in MAIN_EXCLUDED_PAIRS:
            raise PublicDataError("EXCLUDED_IDR_PAIR")
        return self._get("/api/v2/trades?" + urlencode({
            "symbol":pair.replace("_","").upper(),"limit":500,
        }))


def summarize_v2_trade_page_research(payload: Any, *, requested_epoch: int,
                                     received_epoch: int) -> dict:
    """Closed PITIDEN intervals, measured from exactly one public page.

    quoteQty is IDR for IDR symbols. Bracketing with earlier and later
    trades establishes temporal reach, not independently complete tape.
    Do not fill unknown intervals with zero or derive signed order flow.
    """
    from .m05_pitiden_premium_layout import interval_windows
    if (type(requested_epoch) is not int or type(received_epoch) is not int
            or not 86400 < requested_epoch <= received_epoch
            or received_epoch-requested_epoch>30
            or type(payload) is not list or len(payload)>500):
        raise PublicDataError("V2_TRADE_PAGE_SCOPE_INVALID")
    trades = []
    seen = set()
    for item in payload:
        if type(item) is not dict:
            raise PublicDataError("V2_TRADE_ROW_INVALID")
        tid, stamp = item.get("id"),item.get("time")
        if (type(tid) is not int or not 0<tid<2**64 or tid in seen
                or type(stamp) is not int or not 100_000_000_000<=stamp<=received_epoch*1000+999
                or type(item.get("isBestMatch")) is not bool):
            raise PublicDataError("V2_TRADE_ID_OR_TIME_INVALID")
        raw_price,raw_qty,raw_quote=(item.get(key) for key in ("price","qty","quoteQty"))
        if any(type(v) is not str or len(v)>72 for v in (raw_price,raw_qty,raw_quote)):
            raise PublicDataError("V2_TRADE_AMOUNT_FORMAT_INVALID")
        price=_number(raw_price,"V2_PRICE")
        qty=_number(raw_qty,"V2_QTY")
        quote=_number(raw_quote,"V2_QUOTE",zero=True)
        if any(abs(v.adjusted())>20 for v in (price,qty,quote)):
            raise PublicDataError("V2_TRADE_AMOUNT_RANGE_INVALID")
        seen.add(tid)
        trades.append((stamp,tid,price,quote))
    # The endpoint returns recent trades, but ID consecutiveness or
    # exchange completeness is not documented as guaranteed.
    trades.sort()
    oldest = trades[0][0] if trades else None
    newest = trades[-1][0] if trades else None
    windows=[]
    for label,start,end in interval_windows(requested_epoch):
        if label=="24h":
            continue  # Existing rolling summary already contains this.
        a,b=start*1000,end*1000
        selected=[x for x in trades if a<=x[0]<b]
        bracketed=bool(trades and oldest<a and newest>=b)
        with localcontext() as ctx:
            ctx.prec=100
            amount=sum((x[3] for x in selected),Decimal(0))
        windows.append({
            "tf":label,"start_epoch":start,"end_epoch":end,
            "trade_count_observed":len(selected),
            "sample_quote_idr":format(amount,"f") if selected else None,
            "page_bracketed":bracketed,
            "coverage":"PAGE_BRACKETED_UNATTESTED" if bracketed else
                       "PARTIAL_PAGE" if selected else "NO_TRADES_OBSERVED_UNVERIFIED",
            "first_observed_price_idr":format(selected[0][2],"f") if selected else None,
            "last_observed_price_idr":format(selected[-1][2],"f") if selected else None,
            "boundary_price_order_unverified":bool(selected and
                (sum(x[0]==selected[0][0] for x in selected)>1 or
                 sum(x[0]==selected[-1][0] for x in selected)>1)),
            "full_volume_verified":False,
        })
    return {"page_trade_count":len(trades),"page_at_limit":len(trades)==500,
            "oldest_trade_ms":oldest,"newest_trade_ms":newest,
            "page_span_seconds":(newest-oldest)//1000 if trades else None,
            "timeframes":windows,"complete_trade_tape_verified":False,
            "g1_verified":False,"execution_allowed":False}
