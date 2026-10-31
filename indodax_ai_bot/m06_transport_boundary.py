"""M06 offline private GET transport seam. No network implementation or GO grant.

Only an owner-approved separate VPS release may implement actual HTTPS. This
seam validates requests and rejects credentials leaking via diagnostic repr.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Any
from .m06_signed_readonly import prepare_signed_get
from .m06_private_readonly import READ_ONLY_PATHS, M06ReadOnlyBlocked
from .m06_private_payloads import parse_account, parse_open_orders, parse_orders, parse_fills


@dataclass(frozen=True)
class ReadOnlyBundle:
    account: object
    open_orders: tuple
    order_history: tuple
    fills: tuple
    authenticated: bool = False
    history_complete: bool = False
    open_orders_complete: bool = False
    execution_allowed: bool = False


class OfflineM06Transport:
    """Explicit injected fixture transport, no HTTP and no privilege upgrade."""
    def __init__(self, fixture_reader: Callable[[str, str], Any]):
        if not callable(fixture_reader):
            raise ValueError("M06_FIXTURE_READER_REQUIRED")
        self._fixture_reader = fixture_reader

    def fetch(self, *, path: str, query: str) -> Any:
        if path not in READ_ONLY_PATHS or type(query) is not str:
            raise M06ReadOnlyBlocked("M06_PRIVATE_PATH_DENIED")
        # Only test fixtures, not a network transport. Results stay untrusted.
        return self._fixture_reader(path, query)


def inspect_offline_private_bundle(*, transport: OfflineM06Transport,
        api_key: str, secret_key: str, timestamp_ms: int, symbol: str) -> ReadOnlyBundle:
    """Exercise precise signing/parsing with injected offline responses only."""
    if type(transport) is not OfflineM06Transport:
        raise M06ReadOnlyBlocked("M06_REAL_TRANSPORT_NOT_AUTHORIZED")
    paths = (
        "/api/v2/account", "/api/v2/openOrders",
        "/api/v2/order/histories", "/api/v2/myTrades",
    )
    observations = {}
    for path in paths:
        signed = prepare_signed_get(
            path=path, api_key=api_key, secret_key=secret_key,
            timestamp_ms=timestamp_ms,
            symbol=symbol if path != "/api/v2/account" else None,
        )
        # The fixture receives NO credential header or signature.
        observations[path] = transport.fetch(path=path, query=signed.query)
    return ReadOnlyBundle(
        account=parse_account(observations["/api/v2/account"]),
        open_orders=parse_open_orders(observations["/api/v2/openOrders"], expected_symbol=symbol),
        order_history=parse_orders(observations["/api/v2/order/histories"],expected_symbol=symbol),
        fills=parse_fills(observations["/api/v2/myTrades"],expected_symbol=symbol),
    )
