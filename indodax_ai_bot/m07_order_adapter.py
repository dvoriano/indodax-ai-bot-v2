"""Official INDODAX TAPI V2 signed order/status/cancel *offline* contract.

Original V2 M07-01 implementation: one request builder, NEVER an HTTP client.
The only public methods that resemble trading always raise M07_HARDLOCKED.
No env flag, Telegram PASS, injected transport or API key can unlock it.
Reference: INDODAX-TradeAPI-2.md, TAPI V2 POST/GET/DELETE /api/v2/order.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import hmac
import re
from urllib.parse import urlencode


class M07Hardlocked(RuntimeError):
    """Indodax execution is explicitly unavailable until a separate release."""


_SYMBOL = re.compile(r"[A-Z0-9]{2,20}IDR\Z")
_CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{1,36}\Z")
_ENDPOINT = "/api/v2/order"
_FORBIDDEN_BASE = frozenset(("PEPE", "FLOKI", "USDT", "USDC"))


def _symbol(symbol: str) -> str:
    if (type(symbol) is not str or not _SYMBOL.fullmatch(symbol)
            or symbol[:-3] in _FORBIDDEN_BASE):
        raise ValueError("M07_SYMBOL_NOT_ADMITTED")
    return symbol


def _client_id(value: str) -> str:
    if type(value) is not str or not _CLIENT_ID.fullmatch(value):
        raise ValueError("M07_CLIENT_ID_INVALID")
    return value


def _decimal(value: Decimal, field: str) -> str:
    if (type(value) is not Decimal or not value.is_finite()
            or value <= 0 or value.adjusted() > 20
            or value.adjusted() < -18):
        raise ValueError("M07_" + field + "_INVALID")
    return format(value, "f")


@dataclass(frozen=True)
class M07OrderDraft:
    """Explicit idempotency reference; no implied balance/ownership approval."""
    symbol: str
    side: str
    order_type: str
    client_order_id: str
    price: Decimal | None = None
    quantity: Decimal | None = None
    quote_order_qty: int | None = None

    def parameters(self) -> tuple[tuple[str, str], ...]:
        sym = _symbol(self.symbol)
        client = _client_id(self.client_order_id)
        if self.side not in ("BUY", "SELL") or self.order_type not in ("LIMIT", "MARKET"):
            raise ValueError("M07_ORDER_SIDE_OR_TYPE_INVALID")
        params = [
            ("symbol", sym), ("side", self.side),
            ("type", self.order_type),
        ]
        if self.order_type == "LIMIT":
            if (self.price is None or self.quantity is None
                    or self.quote_order_qty is not None):
                raise ValueError("M07_LIMIT_FIELDS_INVALID")
            params.extend((
                ("price", _decimal(self.price, "PRICE")),
                ("quantity", _decimal(self.quantity, "QTY")),
                ("timeInForce", "GTC"),
            ))
        elif self.side == "BUY":
            if (self.price is not None or self.quantity is not None
                    or type(self.quote_order_qty) is not int
                    or self.quote_order_qty <= 0
                    or self.quote_order_qty > 10**12):
                raise ValueError("M07_MARKET_BUY_IDR_FIELDS_INVALID")
            params.append(("quoteOrderQty", str(self.quote_order_qty)))
        else:
            if (self.price is not None or self.quote_order_qty is not None
                    or self.quantity is None):
                raise ValueError("M07_MARKET_SELL_FIELDS_INVALID")
            params.append(("quantity", _decimal(self.quantity, "QTY")))
        params.append(("newClientOrderId", client))
        return tuple(params)


@dataclass(frozen=True, repr=False)
class PreparedM07Request:
    """Sensitive signed envelope: NOT a request ready for network dispatch."""
    method: str
    path: str
    query: str
    body: str
    headers: dict[str, str]
    network_allowed: bool = False
    execution_allowed: bool = False

    def __repr__(self) -> str:
        return "<PreparedM07Request REDACTED hardlocked=True>"


class OfficialV2OrderAdapter:
    """Exact V2 request formats; no HTTP transport or unlock mechanism."""

    @staticmethod
    def contract_status() -> dict:
        return {
            "stage": "M07_OFFICIAL_TAPIV2_SIGNED_ORDER_ADAPTER",
            "status": "HARDLOCKED_NO_PRIVATE_HTTP_OR_ORDER",
            "api_version": "TAPI_V2",
            "create_method": "POST",
            "status_method": "GET",
            "cancel_method": "DELETE",
            "order_endpoint": _ENDPOINT,
            "hmac_sha256_signing_supported_offline": True,
            "requires_distinct_live_go": True,
            "authenticated_account_scope_verified": False,
            "idempotent_reservation_verified": False,
            "order_status_reconciled": False,
            "unknown_order_state_must_not_retry": True,
            "network_allowed": False,
            "execution_allowed": False,
        }

    @staticmethod
    def _sign(*, method: str, params: tuple[tuple[str, str], ...],
              api_key: str, secret_key: str,
              timestamp_ms: int, recv_window_ms: int) -> PreparedM07Request:
        # Credentials are never stored on the adapter, logged or displayed.
        if (type(api_key) is not str or type(secret_key) is not str
                or not 1 <= len(api_key) <= 256
                or not 1 <= len(secret_key) <= 256):
            raise ValueError("M07_CREDENTIAL_SHAPE_INVALID")
        if (type(timestamp_ms) is not int
                or not 1_000_000_000_000 <= timestamp_ms <= 9_999_999_999_999
                or type(recv_window_ms) is not int
                or not 1000 <= recv_window_ms <= 5000):
            raise ValueError("M07_TIMESTAMP_INVALID")
        if method not in ("POST", "GET", "DELETE"):
            raise ValueError("M07_METHOD_DENIED")
        encoded = urlencode((
            *params,
            ("timestamp", str(timestamp_ms)),
            ("recvWindow", str(recv_window_ms)),
        ))
        digest = hmac.new(
            secret_key.encode("utf-8"), encoded.encode("ascii"), sha256
        ).hexdigest()
        return PreparedM07Request(
            method=method,
            path=_ENDPOINT,
            query="" if method == "POST" else encoded,
            body=encoded if method == "POST" else "",
            headers={
                "Accept": "application/json",
                "X-APIKEY": api_key,
                "Sign": digest,
                **({"Content-Type": "application/x-www-form-urlencoded"}
                   if method == "POST" else {}),
            },
        )

    def prepare_order_offline(
        self, draft: M07OrderDraft, *, api_key: str, secret_key: str,
        timestamp_ms: int, recv_window_ms: int = 5000,
    ) -> PreparedM07Request:
        if type(draft) is not M07OrderDraft:
            raise ValueError("M07_ORDER_DRAFT_REQUIRED")
        return self._sign(
            method="POST", params=draft.parameters(),
            api_key=api_key, secret_key=secret_key,
            timestamp_ms=timestamp_ms, recv_window_ms=recv_window_ms,
        )

    def prepare_status_offline(
        self, *, symbol: str, orig_client_order_id: str,
        api_key: str, secret_key: str, timestamp_ms: int,
        recv_window_ms: int = 5000,
    ) -> PreparedM07Request:
        return self._sign(
            method="GET", params=(
                ("symbol", _symbol(symbol)),
                ("origClientOrderId", _client_id(orig_client_order_id)),
            ),
            api_key=api_key, secret_key=secret_key,
            timestamp_ms=timestamp_ms, recv_window_ms=recv_window_ms,
        )

    def prepare_status_viewonly_offline(
        self, *, symbol: str, orig_client_order_id: str,
        api_key: str, secret_key: str, timestamp_ms: int,
        recv_window_ms: int = 5000,
    ) -> PreparedM07Request:
        """Read existing account orders, including trading-excluded assets.

        This permits GET only; it never changes which symbols M07OrderDraft
        may trade. FLOKI/PEPE/USDT/USDC are audit-only here.
        """
        if (type(symbol) is not str
                or not _SYMBOL.fullmatch(symbol)):
            raise ValueError("M07_VIEWONLY_SYMBOL_INVALID")
        return self._sign(
            method="GET", params=(
                ("symbol", symbol),
                ("origClientOrderId", _client_id(orig_client_order_id)),
            ),
            api_key=api_key, secret_key=secret_key,
            timestamp_ms=timestamp_ms, recv_window_ms=recv_window_ms,
        )

    def prepare_cancel_offline(
        self, *, symbol: str, orig_client_order_id: str,
        api_key: str, secret_key: str, timestamp_ms: int,
        recv_window_ms: int = 5000,
    ) -> PreparedM07Request:
        return self._sign(
            method="DELETE", params=(
                ("symbol", _symbol(symbol)),
                ("origClientOrderId", _client_id(orig_client_order_id)),
            ),
            api_key=api_key, secret_key=secret_key,
            timestamp_ms=timestamp_ms, recv_window_ms=recv_window_ms,
        )

    @staticmethod
    def _inspect_nonlive_order_result_shape(
            *, draft: M07OrderDraft, response: dict | None,
            observed_at_ms: int, method: str) -> dict:
        """Exact TAPI V2 CREATE vs GET response grammar, NO live issuer.

        Official POST /api/v2/order response has price, origQty,
        executedQty, orderId and fullOrderId, but NO status field.
        GET /api/v2/order is the separate operation that has status.
        A fake POST accepted shape cannot establish fill/ownership.
        """
        import json
        from decimal import InvalidOperation
        if (type(draft) is not M07OrderDraft
                or type(observed_at_ms) is not int
                or not 1_000_000_000_000 <= observed_at_ms <= 9_999_999_999_999
                or method not in ("POST", "GET")):
            raise ValueError("M07_SENDER_SHAPE_INPUT_INVALID")
        params = draft.parameters()
        request_sha = sha256(json.dumps(
            params, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        if response is None:
            return {
                "source_status": "OFFLINE_TIMEOUT_UNKNOWN_NO_RETRY",
                "response_endpoint_method": method,
                "draft_sha256": request_sha,
                "mock_response_sha256": None,
                "mock_status": None,
                "mock_cumulative_qty": None,
                "mock_exchange_order_id": None,
                "mock_response_shape_matched": False,
                "real_post_response_authenticated": False,
                "retry_authorized": False,
                "order_sent": False, "execution_allowed": False,
            }
        common = {
            "symbol", "clientOrderId", "side", "type", "price",
            "origQty", "executedQty", "orderId", "fullOrderId",
        }
        required = common if method == "POST" else common | {"status", "time"}
        optional = set() if method == "POST" else {"stopPrice"}
        if (type(response) is not dict
                or not required.issubset(response)
                or not set(response).issubset(required | optional)
                or response.get("symbol") != draft.symbol
                or response.get("clientOrderId") != draft.client_order_id
                or response.get("side") != draft.side
                or response.get("type") != draft.order_type
                or type(response.get("orderId")) is not int
                or not 1 <= response["orderId"] <= 10**18
                or type(response.get("fullOrderId")) is not str
                or not 1 <= len(response["fullOrderId"]) <= 100
                or re.fullmatch(r"[A-Za-z0-9_-]+",
                                response["fullOrderId"]) is None
                or any(type(response.get(k)) is not str
                       or not 1 <= len(response[k]) <= 45
                       for k in ("price", "origQty", "executedQty"))):
            raise ValueError("M07_SENDER_MOCK_RESULT_IDENTITY_INVALID")
        status = None
        if method == "GET":
            status = response["status"]
            if (status not in (
                    "NEW","PARTIALLY_FILLED","FILLED",
                    "CANCELLED","REJECTED")
                    or type(response["time"]) is not int
                    or not 1_000_000_000_000 <= response["time"] <= observed_at_ms
                    or ("stopPrice" in response and
                        type(response["stopPrice"]) is not str)):
                raise ValueError("M07_SENDER_MOCK_RESULT_STATUS_INVALID")
        try:
            price = Decimal(response["price"])
            orig = Decimal(response["origQty"])
            executed = Decimal(response["executedQty"])
            valid = (
                draft.order_type == "LIMIT"
                and draft.price is not None
                and draft.quantity is not None
                and all(x.is_finite() for x in (price,orig,executed))
                and price == draft.price
                and orig == draft.quantity
                and 0 <= executed <= orig
                and (method != "GET" or (
                    (status not in ("NEW","REJECTED") or executed == 0)
                    and (status != "PARTIALLY_FILLED" or 0 < executed < orig)
                    and (status != "FILLED" or executed == orig)))
            )
        except (TypeError, ValueError, InvalidOperation):
            valid=False
        if not valid:
            raise ValueError("M07_SENDER_MOCK_RESULT_FILL_INVALID")
        digest = sha256(json.dumps(
            response, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("ascii")).hexdigest()
        return {
            "source_status": (
                "OFFLINE_FAKE_POST_RESULT_NOT_AUTHENTICATED"
                if method == "POST" else
                "OFFLINE_FAKE_GET_STATUS_NOT_AUTHENTICATED"),
            "response_endpoint_method": method,
            "draft_sha256": request_sha,
            "mock_response_sha256": digest,
            "mock_status": (
                "POST_ACK_WITHOUT_ORDER_STATUS" if method == "POST"
                else status),
            "mock_cumulative_qty": str(executed),
            "mock_exchange_order_id": response["orderId"],
            "mock_response_shape_matched": True,
            "real_post_response_authenticated": False,
            "retry_authorized": False,
            "order_sent": False, "execution_allowed": False,
        }

    @staticmethod
    def inspect_nonlive_post_result_shape(
            *, draft: M07OrderDraft,
            response: dict | None,
            submitted_at_ms: int) -> dict:
        return OfficialV2OrderAdapter._inspect_nonlive_order_result_shape(
            draft=draft,response=response,observed_at_ms=submitted_at_ms,
            method="POST",
        )

    @staticmethod
    def inspect_nonlive_get_order_status_shape(
            *, draft: M07OrderDraft,
            response: dict | None,
            observed_at_ms: int) -> dict:
        return OfficialV2OrderAdapter._inspect_nonlive_order_result_shape(
            draft=draft,response=response,observed_at_ms=observed_at_ms,
            method="GET",
        )

    def create_order(self, *args, **kwargs) -> None:
        raise M07Hardlocked("M07_LIVE_CREATE_HARDLOCKED")

    def get_order(self, *args, **kwargs) -> None:
        raise M07Hardlocked("M07_PRIVATE_STATUS_HARDLOCKED")

    def cancel_order(self, *args, **kwargs) -> None:
        raise M07Hardlocked("M07_LIVE_CANCEL_HARDLOCKED")

    def dispatch(self, *args, **kwargs) -> None:
        raise M07Hardlocked("M07_ALL_HTTP_DISPATCH_HARDLOCKED")


@dataclass(frozen=True)
class M07OfflineStatusObservation:
    """Normalized SYNTHETIC status, never authenticated exchange evidence.

    This is deliberately not a private API parser. Before any real recovery,
    a separately authorized, authenticated transport must bind and verify
    exchange response, account and order ownership.
    """
    intent_id: str
    client_order_id: str
    symbol: str
    observation_id: str
    status: str
    cumulative_base: Decimal
    cumulative_quote: Decimal
    observed_epoch: int

    def validated_record(self) -> tuple[str, ...]:
        if (type(self.intent_id) is not str
                or not _CLIENT_ID.fullmatch(self.intent_id)
                or type(self.observation_id) is not str
                or not _CLIENT_ID.fullmatch(self.observation_id)
                or self.client_order_id != self.intent_id):
            raise ValueError("M07_OBSERVATION_ID_INVALID")
        _symbol(self.symbol)
        allowed = (
            "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED",
            "EXPIRED", "REJECTED", "UNKNOWN",
        )
        if type(self.status) is not str or self.status not in allowed:
            raise ValueError("M07_OBSERVATION_STATUS_INVALID")
        if (type(self.observed_epoch) is not int
                or not 1 <= self.observed_epoch <= 9999999999):
            raise ValueError("M07_OBSERVATION_EPOCH_INVALID")
        quantities = []
        for value in (self.cumulative_base, self.cumulative_quote):
            if (type(value) is not Decimal or not value.is_finite()
                    or value < 0 or value >= Decimal("1e24")
                    or len(value.as_tuple().digits) > 48
                    or (value != 0 and value.adjusted() < -18)):
                raise ValueError("M07_OBSERVATION_CUMULATIVE_INVALID")
            quantities.append(format(value, "f"))
        base, quote = quantities
        # These are internal lifecycle invariants, NOT evidence of actual fills.
        if self.status in ("NEW", "REJECTED") and (
                self.cumulative_base != 0 or self.cumulative_quote != 0):
            raise ValueError("M07_OBSERVATION_NONZERO_NEW_OR_REJECTED")
        if self.status in ("PARTIALLY_FILLED", "FILLED") and (
                self.cumulative_base <= 0 or self.cumulative_quote <= 0):
            raise ValueError("M07_OBSERVATION_MISSING_CUMULATIVE")
        return (
            self.intent_id, self.observation_id, self.client_order_id,
            self.symbol, self.status, base, quote, str(self.observed_epoch),
        )
