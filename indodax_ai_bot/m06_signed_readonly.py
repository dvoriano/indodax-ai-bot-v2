"""Pure TAPI V2 authenticated GET request preparation, network never available.

Official Trade API V2: X-APIKEY and HMAC-SHA256 Sign of exact encoded query.
No credentials are stored, no HTTP is performed, and no account trust is granted.
"""
from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlencode
import hashlib
import hmac
import re

from .m06_private_readonly import READ_ONLY_PATHS, M06ReadOnlyBlocked

_ALLOWED_PARAMS = frozenset(("symbol", "startTime", "endTime", "limit", "sort", "timestamp", "recvWindow"))
_SYMBOL = re.compile(r"^[a-z0-9]{2,24}idr$")
_HISTORY = frozenset(("/api/v2/order/histories", "/api/v2/myTrades"))


@dataclass(frozen=True, repr=False)
class PreparedPrivateGET:
    path: str
    query: str
    headers: dict[str, str]
    execution_allowed: bool = False

    def __repr__(self):
        return "<PreparedPrivateGET REDACTED network_disabled=True>"


def prepare_signed_get(*, path: str, api_key: str, secret_key: str,
                       timestamp_ms: int, symbol: str | None = None,
                       start_ms: int | None = None, end_ms: int | None = None,
                       limit: int | None = None, recv_window_ms: int = 5000) -> PreparedPrivateGET:
    """Build official signed request bytes from explicit offline inputs.

    Never return or log this object in production. Calling this function
    is NOT authorization for authenticated networking.
    """
    if path not in READ_ONLY_PATHS:
        raise M06ReadOnlyBlocked("M06_PRIVATE_PATH_DENIED")
    if (type(api_key) is not str or type(secret_key) is not str
            or not 1 <= len(api_key) <= 256 or not 1 <= len(secret_key) <= 256):
        raise ValueError("M06_CREDENTIAL_SHAPE_INVALID")
    if (type(timestamp_ms) is not int or not 1_000_000_000_000 <= timestamp_ms <= 9_999_999_999_999
            or type(recv_window_ms) is not int or not 1000 <= recv_window_ms <= 5000):
        raise ValueError("M06_TIMESTAMP_INVALID")
    if symbol is not None and (type(symbol) is not str or not _SYMBOL.fullmatch(symbol)):
        raise ValueError("M06_SYMBOL_INVALID")
    if path in _HISTORY and symbol is None:
        raise ValueError("M06_HISTORY_SYMBOL_REQUIRED")
    if path not in _HISTORY and any(x is not None for x in (start_ms, end_ms, limit)):
        raise ValueError("M06_UNSUPPORTED_ENDPOINT_PARAM")
    if path == "/api/v2/account" and symbol is not None:
        raise ValueError("M06_ACCOUNT_UNSUPPORTED_SYMBOL")
    if start_ms is not None or end_ms is not None:
        if (type(start_ms) is not int or type(end_ms) is not int
                or start_ms <= 0 or end_ms <= start_ms or end_ms-start_ms > 7*86400*1000):
            raise ValueError("M06_HISTORY_WINDOW_INVALID")
    if limit is not None and (type(limit) is not int or not 10 <= limit <= 1000):
        raise ValueError("M06_HISTORY_LIMIT_INVALID")
    params: list[tuple[str,str]] = []
    if symbol is not None:
        params.append(("symbol",symbol))
    if start_ms is not None:
        params.extend((("startTime", str(start_ms)), ("endTime", str(end_ms))))
    if limit is not None:
        params.append(("limit",str(limit)))
    params.extend((("timestamp",str(timestamp_ms)),("recvWindow",str(recv_window_ms))))
    query = urlencode(params)
    digest = hmac.new(secret_key.encode("utf-8"), query.encode("ascii"), hashlib.sha256).hexdigest()
    return PreparedPrivateGET(path, query,
        {"Accept":"application/json", "X-APIKEY":api_key,"Sign":digest})


def deny_network(*args, **kwargs):
    raise M06ReadOnlyBlocked("M06_PRIVATE_NETWORK_BLOCKED_PENDING_SCOPED_GO")
