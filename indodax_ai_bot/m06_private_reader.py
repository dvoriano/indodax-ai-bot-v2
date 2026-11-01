"""M06 production-shaped TAPI V2 read-only boundary, network OFF by default.

Only approved methods are GET. HTTP is dependency-injected for offline
acceptance; the shipped constructor has NO network transport. This component
cannot elevate API responses to authorized capital or order permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Any
import json
import time

from .m06_signed_readonly import prepare_signed_get
from .m06_private_readonly import READ_ONLY_PATHS, M06ReadOnlyBlocked
from .m06_private_payloads import (
    parse_account, parse_open_orders, parse_orders, parse_fills,
)


@dataclass(frozen=True)
class ReadOnlyBatch:
    account: object
    open_orders: tuple
    orders: tuple
    fills: tuple
    observed_epoch_ms: int
    authenticated_transport: bool = False
    all_pairs_complete: bool = False
    history_complete: bool = False
    execution_allowed: bool = False


class M06PrivateReader:
    """Transport injected ONLY for deterministic offline tests.

    Real authentication, owner GO, egress coordination, account pinning and
    audit of key permissions must be added before genuine private network use.
    """

    def __init__(self, *, transport: Callable | None = None,
                 offline_fixture: bool = False):
        if transport is not None and (not offline_fixture or not callable(transport)):
            raise M06ReadOnlyBlocked("M06_REAL_PRIVATE_TRANSPORT_REQUIRES_SEPARATE_GO")
        if type(offline_fixture) is not bool:
            raise M06ReadOnlyBlocked("M06_OFFLINE_FLAG_INVALID")
        self._transport = transport if offline_fixture else None
        self._offline_fixture = offline_fixture

    def _get(self, *, path: str, symbol: str | None,
             api_key: str, secret_key: str, timestamp_ms: int):
        if path not in READ_ONLY_PATHS:
            raise M06ReadOnlyBlocked("M06_PRIVATE_PATH_DENIED")
        if self._transport is None:
            raise M06ReadOnlyBlocked("M06_PRIVATE_NETWORK_BLOCKED_PENDING_SCOPED_GO")
        prepared = prepare_signed_get(
            path=path, symbol=symbol, api_key=api_key,
            secret_key=secret_key, timestamp_ms=timestamp_ms,
        )
        # No URL construction or http library in this class. The only
        # injected fixture receives the exact signed GET request.
        response = self._transport("GET", prepared.path,
                                   prepared.query, dict(prepared.headers))
        if isinstance(response, (bytes, str)):
            if len(response) > 2_000_000:
                raise ValueError("M06_PRIVATE_RESPONSE_TOO_LARGE")
            try:
                response = json.loads(response)
            except (ValueError, TypeError):
                raise ValueError("M06_PRIVATE_RESPONSE_NON_JSON") from None
        if type(response) not in (dict, list):
            raise ValueError("M06_PRIVATE_RESPONSE_SCHEMA_INVALID")
        if type(response) is dict and "code" in response:
            raise ValueError("M06_PRIVATE_API_ERROR")
        return response

    def inspect_offline_batch(self, *, symbol: str, api_key: str,
                              secret_key: str, timestamp_ms: int) -> ReadOnlyBatch:
        """Offline fixture path; never account-authenticated despite HMAC."""
        if not self._offline_fixture:
            raise M06ReadOnlyBlocked("M06_OFFLINE_FIXTURE_REQUIRED")
        account = parse_account(self._get(
            path="/api/v2/account", symbol=None, api_key=api_key,
            secret_key=secret_key, timestamp_ms=timestamp_ms))
        # No symbol means all open orders. Parser intentionally rejects a
        # non-empty all-pair response until cross-pair attribution is built.
        pending = parse_open_orders(self._get(
            path="/api/v2/openOrders", symbol=None, api_key=api_key,
            secret_key=secret_key, timestamp_ms=timestamp_ms))
        history = parse_orders(self._get(
            path="/api/v2/order/histories", symbol=symbol,
            api_key=api_key, secret_key=secret_key,
            timestamp_ms=timestamp_ms), expected_symbol=symbol)
        fills = parse_fills(self._get(
            path="/api/v2/myTrades", symbol=symbol,
            api_key=api_key, secret_key=secret_key,
            timestamp_ms=timestamp_ms), expected_symbol=symbol)
        return ReadOnlyBatch(account, pending, history, fills, timestamp_ms)
