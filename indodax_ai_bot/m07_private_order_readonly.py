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
    OfficialV2OrderAdapter, PreparedM07Request, _symbol, _client_id,
)

_HOST = "api.indodax.com"
_MAX_BODY = 65536
_MAX_RESPONSES = 2
_DIGITS = re.compile(r"(?:0|[1-9][0-9]{0,20})(?:\.[0-9]{1,18})?\Z")
_ORDER_STATUS = frozenset((
    "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED",
))


class M07ReadOnlyError(ValueError):
    """Only fixed safe diagnostic codes; never include raw private payloads."""


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
        _symbol(expected_symbol)
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
        if request.path != "/api/v2/account":
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


def read_m07_order_status_once(*, journal, symbol: str,
                               client_order_id: str,
                               api_key: str, secret_key: str) -> dict:
    """One foreground two-GET READ ONLY observation; no automatic retry.

    Caller must have the existing owner-only V2 key file; never display it.
    Account permissions must explicitly be View Only before order GET.
    The authenticated result is recorded separately from synthetic intents.
    """
    try:
        _symbol(symbol)
        _client_id(client_order_id)
    except ValueError:
        raise M07ReadOnlyError("M07_ORDER_QUERY_INVALID") from None
    if (type(api_key) is not str or type(secret_key) is not str
            or not api_key or not secret_key):
        raise M07ReadOnlyError("M07_CREDENTIALS_UNAVAILABLE")
    ts = int(time.time() * 1000)
    request = prepare_signed_get(
        path="/api/v2/account", api_key=api_key,
        secret_key=secret_key, timestamp_ms=ts,
    )
    account_payload = _https_json(request)
    if type(account_payload) is dict and type(account_payload.get("code")) is int:
        raise M07ReadOnlyError("M07_ACCOUNT_API_ERROR")
    try:
        enforce_view_only_permissions(account_payload)
    except M06ScopeDenied:
        raise M07ReadOnlyError("M07_KEY_NOT_PROVEN_VIEW_ONLY") from None
    uid = account_payload.get("uid")
    if type(uid) is not int or uid <= 0:
        raise M07ReadOnlyError("M07_ACCOUNT_UID_INVALID")
    ts = int(time.time() * 1000)
    request = OfficialV2OrderAdapter().prepare_status_offline(
        symbol=symbol, orig_client_order_id=client_order_id,
        api_key=api_key, secret_key=secret_key, timestamp_ms=ts,
    )
    payload = _https_json(request)
    order = normalize_order_status(
        payload, expected_symbol=symbol, expected_client_id=client_order_id,
    )
    # No UID, raw balances, secret or API key in DB/output.
    fingerprint = hashlib.sha256(
        (api_key + ":" + str(uid)).encode("utf-8")
    ).hexdigest()
    result = journal.record_m07_authenticated_readonly_order_status(
        account_fingerprint=fingerprint, order=order,
        observed_ms=int(time.time() * 1000),
    )
    return {
        "status": "M07_EXCHANGE_STATUS_READONLY_OBSERVED",
        "api_version": "TAPI_V2",
        "requests_completed": _MAX_RESPONSES,
        "view_only_permissions_verified": True,
        "order_status": order.status,
        "executed_base_qty_observed": str(order.executed_qty),
        "observation_replayed": result["replay"],
        "recorded_in_original_journal": True,
        "exchange_account_response_authenticated": True,
        "bot_ownership_verified": False,
        "fills_and_fee_history_reconciled": False,
        "spendable_idr_verified": False,
        "unknown_intent_released": False,
        "retry_allowed": False,
        "order_sent": False,
        "execution_allowed": False,
    }
