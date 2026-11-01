"""Owner-scoped M06 Indodax V2 HTTPS GET probe. Never trading authority.

Strict: one manually supplied local approval, <=900s, <=20 lifetime
attempts, four documented GET endpoints, view-only key permission proof,
no redirects/proxies, no response or credential logging, no retry.
The original orchestrator may invoke it only through its separate CLI option.
"""
from __future__ import annotations

import contextvars
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import stat
import time
from dataclasses import dataclass
from typing import Any

from .m06_private_readonly import M06ReadOnlyBlocked, READ_ONLY_PATHS
from .m06_signed_readonly import PreparedPrivateGET, prepare_signed_get
from .m06_private_payloads import parse_account, parse_orders, parse_fills, parse_open_orders
from .m06_source_consistency import inspect_cross_source_consistency
from .m06_account_review import reconcile_readonly_private_snapshot
from .m06_ownership_ledger import M06OwnershipLedger, OwnershipConflict

_HOST = "api.indodax.com"
_APPROVAL_ID = "M06_PRIVATE_READONLY_GO_20261031"  # expired, mock compatibility
_ONE_SHOT_APPROVAL_ID = "M06_PRIVATE_VIEW_ONLY_ONE_SHOT_20261031"  # spent/locked
_RETRY_APPROVAL_ID = "M06_PRIVATE_VIEW_ONLY_RETRY_20261031"
_MAX_BYTES = 1_000_000
# Original one-shot is permanently disabled. Only this explicitly reapproved
# retry, on its own nonrenewable budget, may access private HTTPS.
_RELEASE_APPROVAL_ID = os.environ.get("M06_OWNER_ONE_SHOT_PRIVATE_GO")
_NETWORK_RELEASE_GO = (_RELEASE_APPROVAL_ID == _RETRY_APPROVAL_ID)
_SYMBOL = re.compile(r"^[a-z0-9]{2,24}idr$")
# No direct signed GET may bypass a debit through the one-shot budget.
_BUDGET_DEBITED_FOR_THIS_GET = contextvars.ContextVar("m06_one_shot_debited", default=False)


class M06ScopeDenied(M06ReadOnlyBlocked):
    """Safe, fixed-code failure; never put HTTP responses or secrets in messages."""


@dataclass(frozen=True, repr=False)
class M06ScopedApproval:
    approval_id: str
    issued_ms: int
    expires_ms: int
    max_requests: int
    symbol: str

    def __repr__(self):
        return "<M06ScopedApproval REDACTED>"


@dataclass(frozen=True)
class M06PrivateProbeResult:
    status: str
    request_count_this_run: int
    open_orders_count: int
    order_history_count: int
    fills_count: int
    view_only_permissions_verified: bool
    reconciliation_status: str = "REJECT"
    capital_spendable_verified: bool = False
    protected_rebuy_verified: bool = False
    pending_buy_orders: int = 0
    unmatched_fills_in_window: int = 0
    special_asset_ownership_verified: bool = False
    local_ledger_account_bound: bool = False
    local_rebuy_hold_present: bool = False
    local_manual_floor_present: bool = False
    local_shadow_reservations_present: bool = False
    local_shadow_coverage_complete: bool = False
    local_rebuy_receipts_linked: bool = False
    local_other_account_records_present: bool = False
    local_orphan_fill_records_present: bool = False
    external_peer_coverage_verified: bool = False
    special_pool_local_floors_complete: bool = False
    history_complete: bool = False
    cross_bot_ownership_verified: bool = False
    bot_owned_fills_verified: bool = False
    execution_allowed: bool = False


def _owner_private_file(path: str, *, max_bytes: int = 8192) -> dict:
    """Refuse symlinks, non-owner files, broad permissions or oversized JSON."""
    if type(path) is not str or not path.startswith("/"):
        raise M06ScopeDenied("M06_SECURE_FILE_ABSOLUTE_PATH_REQUIRED")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
        try:
            s = os.fstat(fd)
            if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid()
                    or s.st_mode & 0o077 or s.st_size > max_bytes):
                raise M06ScopeDenied("M06_SECURE_FILE_OWNER_OR_MODE_INVALID")
            data = os.read(fd, max_bytes + 1)
            if len(data) > max_bytes:
                raise M06ScopeDenied("M06_SECURE_FILE_TOO_LARGE")
        finally:
            os.close(fd)
        obj = json.loads(data)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise M06ScopeDenied("M06_SECURE_FILE_UNREADABLE") from None
    if type(obj) is not dict:
        raise M06ScopeDenied("M06_SECURE_FILE_SCHEMA_INVALID")
    return obj


def read_approval(path: str, *, now_ms: int | None = None) -> M06ScopedApproval:
    raw = _owner_private_file(path)
    if set(raw) != {"approval_id", "issued_ms", "expires_ms", "max_requests", "symbol"}:
        raise M06ScopeDenied("M06_GO_SCHEMA_INVALID")
    approval = M06ScopedApproval(**raw)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if (approval.approval_id not in (_APPROVAL_ID, _ONE_SHOT_APPROVAL_ID, _RETRY_APPROVAL_ID)
            or type(approval.issued_ms) is not int or type(approval.expires_ms) is not int
            or type(now) is not int
            or approval.issued_ms <= 0 or not approval.issued_ms <= now < approval.expires_ms
            or approval.expires_ms - approval.issued_ms > 900_000
            or type(approval.max_requests) is not int or not 1 <= approval.max_requests <= 20
            or type(approval.symbol) is not str
            or not _SYMBOL.fullmatch(approval.symbol)):
        raise M06ScopeDenied("M06_GO_EXPIRED_OR_OUT_OF_SCOPE")
    if approval.approval_id in (_ONE_SHOT_APPROVAL_ID, _RETRY_APPROVAL_ID) and (
            approval.max_requests != 4 or approval.symbol != "injidr"):
        raise M06ScopeDenied("M06_ONE_SHOT_SCOPE_INVALID")
    return approval


def read_view_only_credentials(path: str) -> tuple[str, str]:
    raw = _owner_private_file(path)
    if set(raw) != {"api_key", "secret_key"}:
        raise M06ScopeDenied("M06_CREDENTIAL_SCHEMA_INVALID")
    key, secret = raw["api_key"], raw["secret_key"]
    if (type(key) is not str or type(secret) is not str
            or not 1 <= len(key) <= 256 or not 1 <= len(secret) <= 256
            or any(ord(c) < 33 or ord(c) > 126 for c in key + secret)):
        raise M06ScopeDenied("M06_CREDENTIAL_SHAPE_INVALID")
    return key, secret


def enforce_view_only_permissions(payload: object) -> None:
    """Mandatory documented V2 account permission flags; absent/true is denial."""
    if type(payload) is not dict or any(
        payload.get(name) is not False
        for name in ("canTrade", "canWithdraw", "canWithdrawFiat")
    ):
        raise M06ScopeDenied("M06_KEY_NOT_PROVEN_VIEW_ONLY")


class M06RequestBudget:
    """Single permanent scope record and atomic per-attempt budget in one V2 DB."""

    def __init__(self, db_path: str):
        if type(db_path) is not str or not os.path.isabs(db_path):
            raise M06ScopeDenied("M06_BUDGET_PATH_INVALID")
        p = Path(db_path)
        parent = p.parent
        if (not parent.is_dir() or parent.is_symlink()
                or parent.stat().st_uid != os.getuid()
                or parent.stat().st_mode & 0o077):
            raise M06ScopeDenied("M06_BUDGET_DIRECTORY_UNSAFE")
        if p.exists() or p.is_symlink():
            s = p.lstat()
            if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid()
                    or s.st_mode & 0o077 or p.is_symlink()):
                raise M06ScopeDenied("M06_BUDGET_FILE_UNSAFE")
        else:
            try:
                fd = os.open(db_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(fd)
            except OSError:
                raise M06ScopeDenied("M06_BUDGET_CREATE_FAILED") from None
        self.path = db_path
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS m06_go_budget (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                approval_id TEXT NOT NULL,
                issued_ms INTEGER NOT NULL,
                expires_ms INTEGER NOT NULL,
                max_requests INTEGER NOT NULL,
                key_hash TEXT NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            )""")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def claim_fresh_retry_once(self, approval: M06ScopedApproval) -> None:
        """Atomically burn replay claim before the first real retry GET.

        An existing consumed budget cannot be reused. A crash consumes the
        claim without allowing an unsafe second attempt.
        """
        if approval.approval_id != _RETRY_APPROVAL_ID:
            raise M06ScopeDenied("M06_RETRY_APPROVAL_REQUIRED")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT 1 FROM m06_go_budget LIMIT 1").fetchone()
            if row is not None:
                raise M06ScopeDenied("M06_RETRY_BUDGET_ALREADY_USED")
            db.execute("""CREATE TABLE IF NOT EXISTS m06_retry_claim (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                approval_id TEXT NOT NULL
            )""")
            try:
                db.execute("INSERT INTO m06_retry_claim VALUES(1, ?)",
                           (approval.approval_id,))
            except sqlite3.IntegrityError:
                raise M06ScopeDenied("M06_RETRY_ALREADY_CLAIMED") from None

    def debit(self, approval: M06ScopedApproval, *, api_key: str,
              now_ms: int | None = None) -> int:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if type(now) is not int or not approval.issued_ms <= now < approval.expires_ms:
            raise M06ScopeDenied("M06_GO_EXPIRED")
        fingerprint = hashlib.sha256(api_key.encode("ascii")).hexdigest()
        expected = (approval.approval_id, approval.issued_ms,
                    approval.expires_ms, approval.max_requests, fingerprint)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT approval_id,issued_ms,expires_ms,max_requests,key_hash,consumed "
                "FROM m06_go_budget WHERE singleton=1"
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO m06_go_budget VALUES(1,?,?,?,?,?,0)", expected
                )
                count = 0
            else:
                if tuple(row[:5]) != expected:
                    raise M06ScopeDenied("M06_GO_CHANGE_OR_RENEWAL_DENIED")
                count = row[5]
            if count >= approval.max_requests:
                raise M06ScopeDenied("M06_GO_REQUEST_QUOTA_EXHAUSTED")
            db.execute(
                "UPDATE m06_go_budget SET consumed=? WHERE singleton=1", (count + 1,)
            )
            return count + 1


def _read_json_over_verified_tls(request: PreparedPrivateGET) -> Any:
    """Only the four fixed HTTPS GET routes; no caller URL, redirects or proxy."""
    if type(request) is not PreparedPrivateGET or request.path not in READ_ONLY_PATHS:
        raise M06ScopeDenied("M06_PRIVATE_ROUTE_DENIED")
    if (not request.query or any(c in request.query for c in "\r\n#")
            or set(request.headers) != {"Accept", "X-APIKEY", "Sign"}):
        raise M06ScopeDenied("M06_REQUEST_SHAPE_DENIED")
    # A local JSON manifest is NOT sufficient to renew a time-limited owner GO.
    # Explicit new owner approval + reviewed pinned release is required before
    # a real HTTPS request. Mocked offline tests replace this function entirely.
    if not _NETWORK_RELEASE_GO:
        raise M06ScopeDenied("M06_PRIVATE_GO_REQUIRES_NEW_RELEASE")
    if _BUDGET_DEBITED_FOR_THIS_GET.get() is not True:
        raise M06ScopeDenied("M06_GET_WITHOUT_ATOMIC_BUDGET_DENIED")
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    conn = http.client.HTTPSConnection(_HOST, port=443, timeout=5, context=context)
    try:
        conn.request("GET", request.path + "?" + request.query,
                     headers=request.headers)
        response = conn.getresponse()
        if response.status != 200:
            raise M06ScopeDenied("M06_PRIVATE_HTTP_NON_200")
        media = response.getheader("Content-Type", "")
        if not media.lower().split(";", 1)[0].strip() == "application/json":
            raise M06ScopeDenied("M06_PRIVATE_HTTP_NOT_JSON")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > _MAX_BYTES):
            raise M06ScopeDenied("M06_PRIVATE_RESPONSE_TOO_LARGE")
        data = response.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            raise M06ScopeDenied("M06_PRIVATE_RESPONSE_TOO_LARGE")
        return json.loads(data.decode("utf-8"))
    except M06ScopeDenied:
        raise
    except (OSError, ValueError, UnicodeError, http.client.HTTPException):
        raise M06ScopeDenied("M06_PRIVATE_HTTPS_OR_JSON_FAILED") from None
    finally:
        conn.close()


def _validate_all_pair_orders(payload: object) -> tuple:
    if type(payload) is not list or len(payload) > 1000:
        raise M06ScopeDenied("M06_ALL_PAIR_OPEN_ORDERS_INVALID_OR_TRUNCATED")
    if len(payload) == 1000:
        raise M06ScopeDenied("M06_ALL_PAIR_OPEN_ORDERS_POSSIBLY_TRUNCATED")
    seen = set()
    parsed = []
    for row in payload:
        if type(row) is not dict:
            raise M06ScopeDenied("M06_ALL_PAIR_OPEN_ORDER_ROW_INVALID")
        symbol = row.get("symbol")
        if type(symbol) is not str or not _SYMBOL.fullmatch(symbol.lower()):
            # Foreign quote assets cannot be silently excluded from account review.
            raise M06ScopeDenied("M06_NON_IDR_OPEN_ORDER_NEEDS_RECONCILIATION")
        orders = parse_open_orders([row], expected_symbol=symbol.lower())
        if not orders[0].full_order_id:
            raise M06ScopeDenied("M06_OPEN_FULL_ORDER_ID_REQUIRED")
        identity = (orders[0].symbol, orders[0].full_order_id)
        if identity in seen:
            raise M06ScopeDenied("M06_ALL_PAIR_ORDER_DUPLICATE")
        seen.add(identity)
        parsed.extend(orders)
    return tuple(parsed)


def run_scoped_private_probe(*, approval: M06ScopedApproval,
        budget: M06RequestBudget, api_key: str, secret_key: str,
        local_ownership_db: str | None = None) -> M06PrivateProbeResult:
    """One session: account then all-pair orders then pair-specific histories.

    No raw balances, UID, fills or secrets are returned by this function.
    Requests are debited BEFORE attempted send; no retries or re-authorization.
    """
    if (type(approval) is not M06ScopedApproval or type(budget) is not M06RequestBudget
            or type(api_key) is not str or type(secret_key) is not str):
        raise M06ScopeDenied("M06_PROBE_CONTRACT_INVALID")
    if local_ownership_db is not None and type(local_ownership_db) is not str:
        raise M06ScopeDenied("M06_LOCAL_LEDGER_PATH_INVALID")
    # With network enabled, the previous GO is categorically ineligible.
    # The 4-GET budget is durable; no repeat run or new manifest can renew it.
    if _NETWORK_RELEASE_GO and (
            approval.approval_id not in (_ONE_SHOT_APPROVAL_ID, _RETRY_APPROVAL_ID)
            or approval.max_requests != 4 or approval.symbol != "injidr"
            or (_RELEASE_APPROVAL_ID is not None
                and approval.approval_id != _RELEASE_APPROVAL_ID)):
        raise M06ScopeDenied("M06_NETWORK_RELEASE_SCOPE_DENIED")
    if _NETWORK_RELEASE_GO and approval.approval_id == _RETRY_APPROVAL_ID:
        budget.claim_fresh_retry_once(approval)
    def get(path: str, *, symbol: str | None = None, limit: int | None = None):
        now_ms = int(time.time() * 1000)
        # Source and time gated before credentials are used to sign.
        budget.debit(approval, api_key=api_key, now_ms=now_ms)
        prepared = prepare_signed_get(
            path=path, api_key=api_key, secret_key=secret_key,
            timestamp_ms=now_ms, symbol=symbol, limit=limit,
        )
        guard = _BUDGET_DEBITED_FOR_THIS_GET.set(True)
        try:
            return _read_json_over_verified_tls(prepared)
        finally:
            _BUDGET_DEBITED_FOR_THIS_GET.reset(guard)

    # The private API offers account observations, not unrestricted capital
    # permission. All four reads join the original M06 capital/ownership
    # diagnostics before one deliberately non-executable result is emitted.
    from .m06_private_payloads import M06PayloadError
    from .m06_source_consistency import M06ConsistencyBlocked
    from .m06_capital_reconcile import M06ReconciliationBlocked
    try:
        account_raw = get("/api/v2/account")
        enforce_view_only_permissions(account_raw)
        account = parse_account(account_raw)
        local_rebuy = None
        if local_ownership_db is not None:
            # Only the UID returned by this private account response may
            # select a LOCAL ledger partition. No user-supplied account label.
            # A missing/insecure/uninitialized ledger is not a permissions
            # failure and never promotes the observed balance into authority.
            try:
                local_rebuy = M06OwnershipLedger.inspect_existing_account_readonly(
                    db_path=local_ownership_db, account_uid=account.uid,
                )
            except OwnershipConflict:
                local_rebuy = None
        all_orders = _validate_all_pair_orders(get("/api/v2/openOrders"))
        order_history = parse_orders(
            get("/api/v2/order/histories", symbol=approval.symbol, limit=1000),
            expected_symbol=approval.symbol,
        )
        fills = parse_fills(
            get("/api/v2/myTrades", symbol=approval.symbol, limit=1000),
            expected_symbol=approval.symbol,
        )
        if len(order_history) == 1000 or len(fills) == 1000:
            raise M06ScopeDenied("M06_PRIVATE_HISTORY_POSSIBLY_TRUNCATED")
        capital_review = reconcile_readonly_private_snapshot(
            account=account, open_orders=all_orders,
            order_history=order_history, fills=fills,
            symbol=approval.symbol, local_rebuy=local_rebuy,
        )
    except (M06PayloadError, M06ConsistencyBlocked,
            M06ReconciliationBlocked, ValueError):
        # Do not print API response bodies, raw wallet amounts or identifiers.
        raise M06ScopeDenied("M06_PRIVATE_DATA_RECONCILIATION_REJECT") from None
    if (capital_review.status != "REJECT"
            or capital_review.spendable_capital_verified
            or capital_review.execution_allowed):
        raise M06ScopeDenied("M06_CAPITAL_AUTHORITY_PROMOTION_DENIED")
    return M06PrivateProbeResult(
        status="M06_VIEW_ONLY_RECONCILED_REJECT",
        request_count_this_run=4,
        open_orders_count=len(all_orders),
        order_history_count=len(order_history),
        fills_count=len(fills),
        view_only_permissions_verified=True,
        reconciliation_status=capital_review.reason,
        capital_spendable_verified=capital_review.spendable_capital_verified,
        protected_rebuy_verified=capital_review.protected_rebuy_ledger_verified,
        pending_buy_orders=capital_review.pending_buy_orders,
        unmatched_fills_in_window=capital_review.unmatched_fills_in_window,
        special_asset_ownership_verified=capital_review.manual_asset_ownership_verified,
        local_ledger_account_bound=capital_review.local_ledger_account_bound,
        local_rebuy_hold_present=capital_review.local_rebuy_hold_present,
        local_manual_floor_present=capital_review.local_manual_floor_present,
        local_shadow_reservations_present=capital_review.local_shadow_reservations_present,
        local_shadow_coverage_complete=capital_review.local_shadow_coverage_complete,
        local_rebuy_receipts_linked=capital_review.local_rebuy_receipts_linked,
        local_other_account_records_present=capital_review.local_other_account_records_present,
        local_orphan_fill_records_present=capital_review.local_orphan_fill_records_present,
        external_peer_coverage_verified=False,
        special_pool_local_floors_complete=capital_review.special_pool_local_floors_complete,
    )
