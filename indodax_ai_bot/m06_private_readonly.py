"""Indodax Trade API v2 M06 read-only contract, deliberately network-disabled.

Official documented GET-only routes. No API credentials are accepted here;
a real authenticated transport requires a separate owner-scoped GO and review.
https://github.com/btcid/indodax-official-api-docs/blob/master/INDODAX-TradeAPI-2.md
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Any


READ_ONLY_PATHS = frozenset((
    "/api/v2/account",
    "/api/v2/openOrders",
    "/api/v2/order/histories",
    "/api/v2/myTrades",
))
BASE_URL = "https://api.indodax.com"


class M06ReadOnlyBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class PrivateReadOnlyReview:
    """Structural diagnostics from untrusted offline fixtures, NOT live account evidence."""
    endpoint: str
    response_shape_valid: bool
    authenticated: bool = False
    freshness_verified: bool = False
    complete_history: bool = False
    bot_owned_fills_verified: bool = False
    execution_allowed: bool = False


class IndodaxV2PrivateReadOnly:
    """No HTTP client, signer, credential handling or secret access by design.

    This strict boundary prevents simulated responses or booleans from being
    mistaken for exchange-verified wallet, orders, fills or trading authority.
    """
    def __init__(self, *, network_go: bool = False):
        if type(network_go) is not bool or network_go:
            raise M06ReadOnlyBlocked("M06_PRIVATE_GO_NOT_IMPLEMENTED")
        self.network_go = False

    def request(self, *, method: str, path: str, params: Mapping[str, Any] | None = None):
        if method != "GET" or path not in READ_ONLY_PATHS:
            raise M06ReadOnlyBlocked("M06_PRIVATE_METHOD_OR_PATH_DENIED")
        raise M06ReadOnlyBlocked("M06_PRIVATE_NETWORK_BLOCKED_PENDING_SCOPED_GO")

    @staticmethod
    def inspect_fixture(path: str, payload: object) -> PrivateReadOnlyReview:
        """Validate only top-level response shapes without claiming authentication."""
        if path not in READ_ONLY_PATHS:
            raise M06ReadOnlyBlocked("M06_PRIVATE_PATH_DENIED")
        if path == "/api/v2/openOrders":
            valid = type(payload) is list and len(payload) <= 10000 and all(
                isinstance(x, dict) for x in payload
            )
        elif path in ("/api/v2/order/histories", "/api/v2/myTrades"):
            # Pagination, timestamps and order/fill completeness are NOT proven.
            valid = type(payload) in (list, dict)
        else:
            valid = type(payload) is dict
        if not valid:
            raise ValueError("M06_PRIVATE_FIXTURE_SHAPE_INVALID")
        return PrivateReadOnlyReview(path, True)

    @staticmethod
    def inspect_idr_balances(*, free: Decimal, locked: Decimal):
        """Pure arithmetic, sourced nowhere; does not confirm API field mapping."""
        from .m06_shadow_account import _amount
        return {"free_idr_unverified": _amount(free),
                "locked_idr_unverified": _amount(locked),
                "authenticated": False,
                "execution_allowed": False}
