"""One bounded original V2 PUBLIC REST HTTP status/Cloudflare evidence probe.

CLI invokes the original-budgeted _get exactly once for /api/server_time.
No authentication, order, retry, redirect bypass, alternate IP/host, response
body export or proxy/header modification. urllib may follow redirects as in\nthe existing adapter; this program never adds another independent GET.\nUser must explicitly invoke it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from urllib.error import HTTPError

from .public_market import IndodaxPublicMarket, PublicDataError

_FIXED_PATH = "/api/server_time"
_RAY_RE = re.compile(r"[a-zA-Z0-9-]{1,80}\Z")
_OFF_ENV = {
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
}


def _http_error(exc: BaseException) -> HTTPError | None:
    visited = set()
    while isinstance(exc, BaseException) and id(exc) not in visited:
        visited.add(id(exc))
        if isinstance(exc, HTTPError):
            return exc
        next_exc = exc.__cause__
        exc = next_exc if isinstance(next_exc, BaseException) else None
    return None


def _headers_evidence(err: HTTPError) -> dict:
    """No response body or arbitrary headers. Ray is bounded and nonsecret."""
    headers = err.headers
    if headers is None:
        return {
            "cf_ray": None, "cloudflare_server": False,
            "cf_challenge": False, "retry_after_present": False,
            "response_content_type": "UNKNOWN",
        }

    def get(name: str) -> str:
        value = headers.get(name, "")
        return value if isinstance(value, str) else ""

    ray = get("CF-Ray").strip()
    kind = get("Content-Type").lower().split(";", 1)[0].strip()
    kind = kind if kind in ("application/json", "text/html") else "OTHER"
    return {
        "cf_ray": ray if _RAY_RE.fullmatch(ray) else None,
        "cloudflare_server": "cloudflare" in get("Server").lower(),
        "cf_challenge": get("cf-mitigated").strip().lower() == "challenge",
        "retry_after_present": bool(get("Retry-After")),
        "response_content_type": kind,
    }


def collect_one_public_rest_edge_evidence(
    *, env=None, market_factory=IndodaxPublicMarket,
    now_fn=None,
) -> dict:
    env = os.environ if env is None else env
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    out = {
        "status": "NOT_RUN",
        "utc_time": now_fn().isoformat(timespec="seconds"),
        "endpoint": _FIXED_PATH,
        "http_status": None,
        "cf_ray": None,
        "cloudflare_server": False,
        "cf_challenge": False,
        "retry_after_present": False,
        "response_content_type": "UNKNOWN",
        "public_get_attempts": 0,
        "orders_sent": 0,
        "private_api_requests": 0,
        "trading_enabled": False,
    }
    if any(env.get(k) != v for k, v in _OFF_ENV.items()):
        out["status"] = "DENIED_REQUIRED_READONLY_ENV"
        return out

    market = market_factory(timeout=8.0)
    try:
        value = market._get(_FIXED_PATH)
        out["status"] = ("PUBLIC_JSON_OK_UNATTESTED"
                         if isinstance(value, dict)
                         else "PUBLIC_PAYLOAD_UNEXPECTED")
    except PublicDataError as exc:
        err = _http_error(exc)
        if err is None:
            out["status"] = "NON_HTTP_PUBLIC_ADAPTER_FAILURE"
        else:
            out["status"] = "HTTP_RESPONSE_ERROR"
            out["http_status"] = err.code if type(err.code) is int else None
            out.update(_headers_evidence(err))
    out["public_get_attempts"] = market.public_get_attempt_count
    return out


if __name__ == "__main__":
    try:
        print(json.dumps(collect_one_public_rest_edge_evidence(),
                         sort_keys=True))
    except Exception:
        # No exception args; they might contain proxy URLs/tokens or HTTP body.
        print(json.dumps({
            "status": "PUBLIC_EDGE_DIAG_INTERNAL_UNAVAILABLE",
            "orders_sent": 0,
            "private_api_requests": 0,
            "trading_enabled": False,
        }, sort_keys=True))
