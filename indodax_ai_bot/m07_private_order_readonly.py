"""Indodax TAPI V2 original M07 signed READ ONLY order-status observation.

Only two fixed authenticated GET endpoints per explicit CLI call:
GET /api/v2/account proves the API key is View Only before
GET /api/v2/order by caller-supplied client order ID. No POST/DELETE,
retries, redirects, proxies, Telegram calls, trading or fill ownership.
A response from Indodax is account evidence, NOT evidence that this
V2 bot submitted the order. Order not found is NOT proof of no order.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import http.client
import json
import re
import ssl
import time

from .m06_signed_readonly import PreparedPrivateGET, prepare_signed_get
from .m06_scoped_https import (
    enforce_view_only_permissions, M06ScopeDenied,
)
from .m07_order_adapter import (
    OfficialV2OrderAdapter, PreparedM07Request, _SYMBOL, _client_id,
)

_HOST = "api.indodax.com"
_MAX_BODY = 65536
_MAX_RESPONSES = 2
_SEVEN_DAYS_MS = 7 * 86400 * 1000
_DIGITS = re.compile(r"(?:0|[1-9][0-9]{0,20})(?:\.[0-9]{1,18})?\Z")
_ORDER_STATUS = frozenset((
    "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED",
))


class M07ReadOnlyError(ValueError):
    """Only fixed safe diagnostic codes; never include raw private payloads."""


def _readonly_symbol(symbol: str) -> str:
    """Audit every valid IDR symbol, not only MAIN trading candidates."""
    if type(symbol) is not str or not _SYMBOL.fullmatch(symbol):
        raise M07ReadOnlyError("M07_ORDER_QUERY_INVALID")
    return symbol


@dataclass(frozen=True, repr=False)
class M07ReadOnlyOrder:
    symbol: str
    client_order_id: str
    order_id: int
    full_order_id: str
    side: str
    order_type: str
    status: str
    original_qty: Decimal
    executed_qty: Decimal
    created_ms: int

    def __repr__(self) -> str:
        return "<M07ReadOnlyOrder REDACTED; not bot-owned>"


def _decimal_quantity(raw: object) -> Decimal:
    if type(raw) is not str or not _DIGITS.fullmatch(raw):
        raise M07ReadOnlyError("M07_ORDER_QUANTITY_INVALID")
    try:
        result = Decimal(raw)
    except InvalidOperation:
        raise M07ReadOnlyError("M07_ORDER_QUANTITY_INVALID") from None
    if not result.is_finite() or result < 0 or result >= Decimal("1e24"):
        raise M07ReadOnlyError("M07_ORDER_QUANTITY_INVALID")
    return result


def normalize_order_status(
    payload: object, *, expected_symbol: str, expected_client_id: str,
) -> M07ReadOnlyOrder:
    """Validate the documented GET /api/v2/order body, never infer a fill lot."""
    try:
        _readonly_symbol(expected_symbol)
        _client_id(expected_client_id)
    except ValueError:
        raise M07ReadOnlyError("M07_ORDER_QUERY_INVALID") from None
    if type(payload) is not dict:
        raise M07ReadOnlyError("M07_ORDER_RESPONSE_INVALID")
    if type(payload.get("code")) is int:
        if payload["code"] in (-2013, 1112):
            raise M07ReadOnlyError("M07_ORDER_NOT_FOUND_UNRESOLVED")
        raise M07ReadOnlyError("M07_ORDER_API_ERROR_UNRESOLVED")
    if (payload.get("symbol") != expected_symbol
            or payload.get("clientOrderId") != expected_client_id):
        raise M07ReadOnlyError("M07_ORDER_IDENTITY_MISMATCH")
    oid, full = payload.get("orderId"), payload.get("fullOrderId")
    if (type(oid) is not int or oid <= 0
            or type(full) is not str or not 1 <= len(full) <= 128
            or not re.fullmatch(r"[A-Za-z0-9_-]+", full)):
        raise M07ReadOnlyError("M07_ORDER_EXCHANGE_ID_INVALID")
    side, typ, status = (payload.get("side"), payload.get("type"),
                         payload.get("status"))
    if (side not in ("BUY", "SELL") or typ not in ("MARKET", "LIMIT")
            or type(status) is not str or status not in _ORDER_STATUS):
        raise M07ReadOnlyError("M07_ORDER_ENUM_INVALID")
    original = _decimal_quantity(payload.get("origQty"))
    executed = _decimal_quantity(payload.get("executedQty"))
    if original > 0 and executed > original:
        raise M07ReadOnlyError("M07_ORDER_EXECUTED_EXCEEDS_ORIGINAL")
    if status in ("NEW", "REJECTED") and executed != 0:
        raise M07ReadOnlyError("M07_ORDER_STATUS_QUANTITY_CONFLICT")
    if status in ("PARTIALLY_FILLED", "FILLED") and executed <= 0:
        raise M07ReadOnlyError("M07_ORDER_STATUS_QUANTITY_CONFLICT")
    if status == "FILLED" and original > 0 and executed != original:
        raise M07ReadOnlyError("M07_ORDER_STATUS_QUANTITY_CONFLICT")
    ms = payload.get("time")
    if type(ms) is not int or not 1_000_000_000_000 <= ms <= 9_999_999_999_999:
        raise M07ReadOnlyError("M07_ORDER_TIME_INVALID")
    return M07ReadOnlyOrder(
        symbol=expected_symbol, client_order_id=expected_client_id,
        order_id=oid, full_order_id=full, side=side, order_type=typ,
        status=status, original_qty=original, executed_qty=executed,
        created_ms=ms,
    )


def _https_json(request: PreparedPrivateGET | PreparedM07Request) -> object:
    """Fixed host, verified TLS, GET only, bounded response. No auto-retry."""
    if type(request) is PreparedPrivateGET:
        if request.path not in (
                "/api/v2/account", "/api/v2/order/histories",
                "/api/v2/myTrades", "/api/v2/openOrders"):
            raise M07ReadOnlyError("M07_READONLY_PATH_DENIED")
    elif type(request) is PreparedM07Request:
        if (request.path != "/api/v2/order"
                or request.method != "GET" or request.body
                or request.network_allowed or request.execution_allowed):
            raise M07ReadOnlyError("M07_READONLY_METHOD_DENIED")
    else:
        raise M07ReadOnlyError("M07_READONLY_REQUEST_INVALID")
    if (type(request.query) is not str
            or not 1 <= len(request.query) <= 512
            or any(x in request.query for x in "\r\n#")
            or set(request.headers) != {"Accept", "X-APIKEY", "Sign"}
            or request.headers.get("Accept") != "application/json"):
        raise M07ReadOnlyError("M07_READONLY_REQUEST_SHAPE_DENIED")
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    conn = http.client.HTTPSConnection(_HOST, timeout=5, context=ctx)
    try:
        conn.request("GET", request.path + "?" + request.query,
                     headers=request.headers)
        resp = conn.getresponse()
        if resp.status != 200:
            if resp.status == 404:
                raise M07ReadOnlyError("M07_ORDER_NOT_FOUND_UNRESOLVED")
            if resp.status in (401, 403, 429):
                raise M07ReadOnlyError("M07_PRIVATE_HTTP_" + str(resp.status))
            raise M07ReadOnlyError("M07_PRIVATE_HTTP_NON_200")
        media = (resp.getheader("Content-Type", "") or "").split(";", 1)[0]
        if media.lower().strip() != "application/json":
            raise M07ReadOnlyError("M07_PRIVATE_CONTENT_TYPE_INVALID")
        length = resp.getheader("Content-Length")
        if length is not None and (not length.isdigit()
                                   or int(length) > _MAX_BODY):
            raise M07ReadOnlyError("M07_PRIVATE_RESPONSE_TOO_LARGE")
        raw = resp.read(_MAX_BODY + 1)
        if len(raw) > _MAX_BODY:
            raise M07ReadOnlyError("M07_PRIVATE_RESPONSE_TOO_LARGE")
        return json.loads(raw.decode("utf-8"))
    except M07ReadOnlyError:
        raise
    except (OSError, ValueError, UnicodeError, http.client.HTTPException):
        raise M07ReadOnlyError("M07_READONLY_TRANSPORT_OR_JSON_FAILED") from None
    finally:
        conn.close()


def _verify_view_only_uid(*, api_key: str, secret_key: str) -> int:
    ts = int(time.time() * 1000)
    prepared = prepare_signed_get(
        path="/api/v2/account", api_key=api_key,
        secret_key=secret_key, timestamp_ms=ts,
    )
    response = _https_json(prepared)
    if type(response) is dict and type(response.get("code")) is int:
        raise M07ReadOnlyError("M07_ACCOUNT_API_ERROR")
    try:
        enforce_view_only_permissions(response)
    except M06ScopeDenied:
        raise M07ReadOnlyError("M07_KEY_NOT_PROVEN_VIEW_ONLY") from None
    uid = response.get("uid")
    if type(uid) is not int or uid <= 0:
        raise M07ReadOnlyError("M07_ACCOUNT_UID_INVALID")
    return uid


def _read_and_record_order(*, journal, symbol: str,
                           client_order_id: str, uid: int,
                           api_key: str, secret_key: str,
                           requests_completed: int,
                           history_discovered: bool,
                           history_submit_ms: int | None = None,
                           history_finish_ms: int | None = None,
                           include_fills: bool = False) -> dict:
    ts = int(time.time() * 1000)
    request = OfficialV2OrderAdapter().prepare_status_viewonly_offline(
        symbol=symbol, orig_client_order_id=client_order_id,
        api_key=api_key, secret_key=secret_key, timestamp_ms=ts,
    )
    order = normalize_order_status(
        _https_json(request),
        expected_symbol=symbol,
        expected_client_id=client_order_id,
    )
    if (history_submit_ms is not None
            and order.created_ms != history_submit_ms):
        raise M07ReadOnlyError("M07_HISTORY_ORDER_TIME_MISMATCH")
    fingerprint = hashlib.sha256(
        (api_key + ":" + str(uid)).encode("utf-8")
    ).hexdigest()
    result = journal.record_m07_authenticated_readonly_order_status(
        account_fingerprint=fingerprint, order=order,
        observed_ms=int(time.time() * 1000),
    )
    report = {
        "status": "M07_EXCHANGE_STATUS_READONLY_OBSERVED",
        "api_version": "TAPI_V2",
        "requests_completed": requests_completed,
        "view_only_permissions_verified": True,
        "order_status": order.status,
        "executed_base_qty_observed": str(order.executed_qty),
        "observation_replayed": result["replay"],
        "recorded_in_original_journal": True,
        "discovered_from_recent_history": history_discovered,
        "history_window_full_account_verified": False,
        "exchange_account_response_authenticated": True,
        "bot_ownership_verified": False,
        "fills_and_fee_history_reconciled": False,
        "spendable_idr_verified": False,
        "unknown_intent_released": False,
        "retry_allowed": False,
        "order_sent": False,
        "execution_allowed": False,
    }
    if include_fills:
        # One read-only follow-up to official TAPIv2 myTrades. No implied
        # bot ownership, free balance or right to trade special assets.
        if (order.status != "FILLED"
                or history_submit_ms is None
                or type(history_finish_ms) is not int
                or history_finish_ms < history_submit_ms
                or history_finish_ms > int(time.time()*1000)
                or history_finish_ms - history_submit_ms > (
                    _SEVEN_DAYS_MS - 120000)):
            raise M07ReadOnlyError("M07_FILLS_HISTORY_FINISH_UNVERIFIED")
        from .m07_readonly_fills import reconcile_m07_mytrades
        start_ms = history_submit_ms - 60000
        end_ms = history_finish_ms + 60000
        if end_ms - start_ms > _SEVEN_DAYS_MS:
            raise M07ReadOnlyError("M07_FILLS_WINDOW_TOO_WIDE")
        request = prepare_signed_get(
            path="/api/v2/myTrades", api_key=api_key,
            secret_key=secret_key, timestamp_ms=int(time.time()*1000),
            symbol=symbol.lower(),
            start_ms=start_ms, end_ms=end_ms, limit=1000,
        )
        fills = reconcile_m07_mytrades(
            _https_json(request), order=order,
            window_start_ms=start_ms, window_end_ms=end_ms,
        )
        rec = journal.record_m07_exchange_readonly_fills(
            account_fingerprint=fingerprint, fills=fills,
            observed_ms=int(time.time()*1000),
        )
        report.update({
            "status": "M07_EXCHANGE_FILLS_READONLY_RECONCILED",
            "requests_completed": requests_completed + 1,
            "matched_fill_count": fills.count,
            "executed_base_qty_reconciled": str(fills.executed_base),
            "gross_quote_idr": str(fills.gross_quote),
            "commission_including_tax_idr": str(
                fills.total_commission_including_tax_quote),
            "net_quote_idr": str(fills.net_quote),
            "quote_semantics": ("NET_SELL_PROCEEDS_IDR"
                                if fills.side == "SELL" else
                                "BUY_COST_INCLUDING_FEE_IDR"),
            "fills_and_fee_history_reconciled": True,
            "read_only_fill_journal_replay": rec["replay"],
            "bot_ownership_verified": False,
            "unknown_intent_released": False,
            "execution_allowed": False,
        })
    return report


def read_m07_order_status_once(*, journal, symbol: str,
                               client_order_id: str,
                               api_key: str, secret_key: str) -> dict:
    """Two GETs: owner key permissions + order by exact known client ID."""
    try:
        _readonly_symbol(symbol)
        _client_id(client_order_id)
    except ValueError:
        raise M07ReadOnlyError("M07_ORDER_QUERY_INVALID") from None
    if not api_key or not secret_key:
        raise M07ReadOnlyError("M07_CREDENTIALS_UNAVAILABLE")
    uid = _verify_view_only_uid(api_key=api_key, secret_key=secret_key)
    return _read_and_record_order(
        journal=journal, symbol=symbol, client_order_id=client_order_id,
        uid=uid, api_key=api_key, secret_key=secret_key,
        requests_completed=2, history_discovered=False,
    )


def _latest_history_candidate(
        payload: object, *, symbol: str, allow_empty: bool = False,
        from_ms: int | None = None, until_ms: int | None = None,
        ) -> tuple[str, int] | None:
    """Select latest order, reject untrusted rows and ambiguous timestamps."""
    _readonly_symbol(symbol)
    if (type(payload) is not dict or type(payload.get("data")) is not list
            or type(payload.get("code")) is int):
        raise M07ReadOnlyError("M07_HISTORY_RESPONSE_INVALID")
    rows = payload["data"]
    if len(rows) > 10:
        raise M07ReadOnlyError("M07_HISTORY_LIMIT_INVALID")
    if not rows:
        if allow_empty:
            return None
        raise M07ReadOnlyError("M07_HISTORY_EMPTY_UNRESOLVED")
    best_id, best_epoch, best_finish = None, -1, None
    for row in rows:
        if (type(row) is not dict
                or type(row.get("symbol")) is not str
                or row["symbol"].upper() != symbol):
            raise M07ReadOnlyError("M07_HISTORY_SYMBOL_MISMATCH")
        client_id = row.get("clientOrderId")
        try:
            _client_id(client_id)
        except ValueError:
            raise M07ReadOnlyError("M07_HISTORY_CLIENT_ID_INVALID") from None
        submit_ms = row.get("submitTime")
        if (type(submit_ms) is not int
                or not 1_000_000_000_000 <= submit_ms <= 9_999_999_999_999):
            raise M07ReadOnlyError("M07_HISTORY_SUBMIT_TIME_INVALID")
        if (from_ms is not None and submit_ms < from_ms
                or until_ms is not None and submit_ms > until_ms):
            raise M07ReadOnlyError("M07_HISTORY_OUTSIDE_WINDOW")
        if submit_ms == best_epoch:
            raise M07ReadOnlyError("M07_HISTORY_AMBIGUOUS_LATEST")
        if submit_ms > best_epoch:
            best_id, best_epoch = client_id, submit_ms
            best_finish = row.get("finishTime")
    return best_id, best_epoch, best_finish


def _latest_client_id_from_history(payload: object, *,
                                   symbol: str) -> str:
    """Original 24h last-order path retained for backward compatibility."""
    selected = _latest_history_candidate(payload, symbol=symbol)
    return selected[0]


def read_m07_latest_order_status_once(*, journal, symbol: str,
                                      api_key: str, secret_key: str) -> dict:
    """Three GETs maximum: account → last-24h history → latest order.

    No caller-known order ID needed. Even valid previous account orders are
    NOT treated as V2 bot orders or spendable assets. Empty history never
    causes a second search or unbounded pagination.
    """
    _readonly_symbol(symbol)
    if not api_key or not secret_key:
        raise M07ReadOnlyError("M07_CREDENTIALS_UNAVAILABLE")
    uid = _verify_view_only_uid(api_key=api_key, secret_key=secret_key)
    ts = int(time.time() * 1000)
    prepared = prepare_signed_get(
        path="/api/v2/order/histories",
        api_key=api_key, secret_key=secret_key,
        timestamp_ms=ts, symbol=symbol.lower(), limit=10,
    )
    latest = _latest_history_candidate(
        _https_json(prepared), symbol=symbol,
    )
    return _read_and_record_order(
        journal=journal, symbol=symbol, client_order_id=latest[0],
        uid=uid, api_key=api_key, secret_key=secret_key,
        requests_completed=3, history_discovered=True,
        history_submit_ms=latest[1],
    )

def read_m07_14day_order_status_once(*, journal, symbol: str,
                                     api_key: str, secret_key: str,
                                     include_fills: bool = False) -> dict:
    """One strictly bounded 14-day retrospective View Only diagnostic.

    Exactly 1 account GET + 2 <=7-day histories + at most 1 GET status.
    USDT/PEPE/FLOKI may be OBSERVED but never traded by M07OrderDraft.
    This is NOT full history coverage, ownership or trading authorization.
    """
    _readonly_symbol(symbol)
    if not api_key or not secret_key:
        raise M07ReadOnlyError("M07_CREDENTIALS_UNAVAILABLE")
    uid = _verify_view_only_uid(api_key=api_key, secret_key=secret_key)
    observed_now_ms = int(time.time() * 1000)
    if not 1_000_000_000_000 <= observed_now_ms <= 9_999_999_999_999:
        raise M07ReadOnlyError("M07_OBSERVATION_CLOCK_INVALID")
    start = observed_now_ms - 2 * _SEVEN_DAYS_MS
    mid = observed_now_ms - _SEVEN_DAYS_MS
    windows = ((mid, observed_now_ms), (start, mid - 1))
    latest = None
    for lower, upper in windows:
        signed = prepare_signed_get(
            path="/api/v2/order/histories",
            api_key=api_key, secret_key=secret_key,
            timestamp_ms=int(time.time() * 1000),
            symbol=symbol.lower(), start_ms=lower,
            end_ms=upper, limit=10,
        )
        candidate = _latest_history_candidate(
            _https_json(signed), symbol=symbol, allow_empty=True,
            from_ms=lower, until_ms=upper,
        )
        if candidate is not None:
            if latest is not None and candidate[1] == latest[1]:
                raise M07ReadOnlyError("M07_HISTORY_AMBIGUOUS_LATEST")
            if latest is None or candidate[1] > latest[1]:
                latest = candidate
    if latest is None:
        raise M07ReadOnlyError("M07_HISTORY_14D_EMPTY_UNRESOLVED")
    result = _read_and_record_order(
        journal=journal, symbol=symbol, client_order_id=latest[0],
        uid=uid, api_key=api_key, secret_key=secret_key,
        requests_completed=4, history_discovered=True,
        history_submit_ms=latest[1], history_finish_ms=latest[2],
        include_fills=include_fills,
    )
    result["history_window_last_days"] = 14
    result["history_is_full_account_coverage"] = False
    result["excluded_from_main_trading_if_special_asset"] = (
        symbol[:-3] in ("PEPE", "FLOKI", "USDT", "USDC")
    )
    return result

