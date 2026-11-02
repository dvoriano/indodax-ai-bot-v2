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

    def create_order(self, *args, **kwargs) -> None:
        raise M07Hardlocked("M07_LIVE_CREATE_HARDLOCKED")

    def get_order(self, *args, **kwargs) -> None:
        raise M07Hardlocked("M07_PRIVATE_STATUS_HARDLOCKED")

    def cancel_order(self, *args, **kwargs) -> None:
        raise M07Hardlocked("M07_LIVE_CANCEL_HARDLOCKED")

    def dispatch(self, *args, **kwargs) -> None:
        raise M07Hardlocked("M07_ALL_HTTP_DISPATCH_HARDLOCKED")
