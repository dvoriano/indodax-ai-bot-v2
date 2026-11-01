"""One owner-scoped M06 private READ-ONLY account coverage session.

The existing V2 orchestrator owns this single-use diagnostic. Only signed
GET account, all-pair openOrders, and 7-day bounded histories/myTrades for
INJ/FLOKI/PEPE/USDT-IDR may be sent. Not exchange-wide history attestation,
a capital budget, ownership certificate, or order permission.

Official docs: https://github.com/btcid/indodax-official-api-docs/blob/master/INDODAX-TradeAPI-2.md
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from . import m06_scoped_https as boundary
from .m06_account_review import reconcile_readonly_private_snapshot
from .m06_private_payloads import parse_account, parse_orders, parse_fills
from .m06_signed_readonly import prepare_signed_get
from .m06_ownership_ledger import M06OwnershipLedger, OwnershipConflict

COVERAGE_ID = "M06_PRIVATE_ACCOUNT_COVERAGE_20261101"
START_EPOCH_MS = 1792774800000  # 2026-10-24 00:00 WIB
SEVEN_DAYS_MS = 7 * 86400 * 1000
PAIRS = ("injidr", "flokiidr", "pepeidr", "usdtidr")
MAX_WINDOWS = 2
MAX_REQUESTS = 2 + 2 * len(PAIRS) * MAX_WINDOWS


@dataclass(frozen=True, repr=False)
class M06CoverageApproval:
    approval_id: str
    issued_ms: int
    expires_ms: int
    max_requests: int
    start_ms: int
    end_ms: int
    symbols: tuple[str, ...]

    def __repr__(self):
        return "<M06CoverageApproval REDACTED>"


@dataclass(frozen=True, repr=False)
class M06CoverageResult:
    status: str
    request_count: int
    checked_pairs: int
    checked_windows_per_pair: int
    open_orders_count: int
    history_orders_count: int
    trade_fills_count: int
    local_receipts_matched: int
    local_receipts_unlinked: int
    unresolved_requirements: tuple[str, ...]
    # Evidence-gated per original checklist; observation != LIVE acceptance.
    m06_02_balance_and_open_orders_observed: bool = False
    m06_03_special_pool_ratio_observed: bool = False
    m06_04_local_fill_receipts_in_window_matched: bool = False
    m06_05_local_account_conflict_free: bool = False
    full_account_history_verified: bool = False
    external_peer_coverage_verified: bool = False
    spendable_idr_verified: bool = False
    bot_owned_sellable_verified: bool = False
    execution_allowed: bool = False

    def __repr__(self):
        return "<M06CoverageResult REDACTED REJECT>"


def coverage_windows(start_ms: int, end_ms: int) -> tuple[tuple[int, int], ...]:
    """No default last-24h blindspot and no >7-day private queries."""
    if (type(start_ms) is not int or type(end_ms) is not int
            or start_ms != START_EPOCH_MS or end_ms <= start_ms
            or end_ms - start_ms > MAX_WINDOWS * SEVEN_DAYS_MS):
        raise boundary.M06ScopeDenied("M06_COVERAGE_TIME_SCOPE_INVALID")
    windows = []
    cursor = start_ms
    while cursor < end_ms:
        last = min(cursor + SEVEN_DAYS_MS - 1, end_ms)
        windows.append((cursor, last))
        cursor = last + 1
    if len(windows) != MAX_WINDOWS:
        # Fixed 18 requests is part of the owner's single-use authorization.
        raise boundary.M06ScopeDenied("M06_COVERAGE_REQUIRES_TWO_WINDOWS")
    return tuple(windows)


def read_coverage_approval(path: str, *, now_ms: int | None = None) -> M06CoverageApproval:
    """A new explicit 0600 owner GO, not the spent four-GET GO."""
    raw = boundary._owner_private_file(path)
    expected = {"approval_id", "issued_ms", "expires_ms", "max_requests",
                "start_ms", "end_ms", "symbols"}
    if set(raw) != expected:
        raise boundary.M06ScopeDenied("M06_COVERAGE_GO_SCHEMA_INVALID")
    symbols = raw.get("symbols")
    if type(symbols) is not list or tuple(symbols) != PAIRS:
        raise boundary.M06ScopeDenied("M06_COVERAGE_SYMBOLS_INVALID")
    approval = M06CoverageApproval(
        approval_id=raw["approval_id"], issued_ms=raw["issued_ms"],
        expires_ms=raw["expires_ms"], max_requests=raw["max_requests"],
        start_ms=raw["start_ms"], end_ms=raw["end_ms"],
        symbols=tuple(symbols),
    )
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if (approval.approval_id != COVERAGE_ID
            or type(approval.issued_ms) is not int
            or type(approval.expires_ms) is not int
            or type(approval.max_requests) is not int
            or approval.max_requests != MAX_REQUESTS
            or type(now) is not int
            or not approval.issued_ms <= now < approval.expires_ms
            or approval.expires_ms - approval.issued_ms > 900_000
            or approval.issued_ms <= 0
            or type(approval.end_ms) is not int
            or approval.end_ms > approval.issued_ms):
        raise boundary.M06ScopeDenied("M06_COVERAGE_GO_EXPIRED_OR_INVALID")
    coverage_windows(approval.start_ms, approval.end_ms)
    return approval


# Separate one-GET owner GO. This never renews 20261101 18-GET coverage.
ACCOUNT_DIAG_ID = "M06_PRIVATE_ACCOUNT_DIAGNOSTIC_20261101"


@dataclass(frozen=True, repr=False)
class M06AccountDiagnosticResult:
    status: str = "M06_ACCOUNT_DIAGNOSTIC_READONLY_OK"
    request_count: int = 1
    view_only_permissions_verified: bool = True
    balances_schema_observed: bool = True
    spendable_idr_verified: bool = False
    manual_asset_ownership_verified: bool = False
    external_peer_coverage_verified: bool = False
    execution_allowed: bool = False

    def __repr__(self):
        return "<M06AccountDiagnosticResult REDACTED NOT_TRADABLE>"


def read_account_diagnostic_approval(
        path: str, *, now_ms: int | None = None
        ) -> boundary.M06ScopedApproval:
    """Validate a fresh explicit GO for exactly ONE account GET."""
    raw = boundary._owner_private_file(path)
    if set(raw) != {"approval_id", "issued_ms", "expires_ms",
                    "max_requests", "symbol"}:
        raise boundary.M06ScopeDenied("M06_ACCOUNT_DIAG_GO_SCHEMA_INVALID")
    approval = boundary.M06ScopedApproval(**raw)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if (approval.approval_id != ACCOUNT_DIAG_ID
            or type(approval.issued_ms) is not int
            or type(approval.expires_ms) is not int
            or type(now) is not int
            or not approval.issued_ms <= now < approval.expires_ms
            or approval.expires_ms - approval.issued_ms > 900000
            or approval.max_requests != 1
            or type(approval.max_requests) is not int
            or approval.symbol != "injidr"):
        raise boundary.M06ScopeDenied("M06_ACCOUNT_DIAG_GO_INVALID")
    return approval


def run_account_diagnostic_readonly_once(*,
        approval: boundary.M06ScopedApproval,
        budget: boundary.M06RequestBudget,
        api_key: str, secret_key: str) -> M06AccountDiagnosticResult:
    """Only GET /api/v2/account; no order history, balance values or LIVE."""
    if (type(approval) is not boundary.M06ScopedApproval
            or type(budget) is not boundary.M06RequestBudget
            or type(api_key) is not str or type(secret_key) is not str
            or approval.approval_id != ACCOUNT_DIAG_ID
            or approval.max_requests != 1 or approval.symbol != "injidr"
            or boundary._RELEASE_APPROVAL_ID != ACCOUNT_DIAG_ID
            or boundary._NETWORK_RELEASE_GO is not True):
        raise boundary.M06ScopeDenied("M06_ACCOUNT_DIAG_RELEASE_DENIED")
    now = int(time.time() * 1000)
    if not approval.issued_ms <= now < approval.expires_ms:
        raise boundary.M06ScopeDenied("M06_ACCOUNT_DIAG_GO_EXPIRED")
    # Burn before preparing/sending the single authenticated request.
    budget.claim_fresh_account_diagnostic_once(ACCOUNT_DIAG_ID)
    budget.debit(approval, api_key=api_key, now_ms=now)
    try:
        signed = prepare_signed_get(
            path="/api/v2/account", api_key=api_key,
            secret_key=secret_key, timestamp_ms=now,
        )
    except (ValueError, UnicodeError):
        raise boundary.M06ScopeDenied("M06_ACCOUNT_DIAG_SIGNING_FAILED") from None
    token = boundary._BUDGET_DEBITED_FOR_THIS_GET.set(True)
    try:
        payload = boundary._read_json_over_verified_tls(signed)
    finally:
        boundary._BUDGET_DEBITED_FOR_THIS_GET.reset(token)
    # Indodax may return an error JSON even with HTTP 200.
    if type(payload) is dict and type(payload.get("code")) is int:
        known = {
            -1002: "M06_ACCOUNT_DIAG_API_CREDENTIAL_REJECTED",
            -1021: "M06_ACCOUNT_DIAG_API_TIMESTAMP_REJECTED",
            -1022: "M06_ACCOUNT_DIAG_API_SIGNATURE_REJECTED",
            -2015: "M06_ACCOUNT_DIAG_API_ACCESS_REJECTED",
            -1003: "M06_ACCOUNT_DIAG_API_RATE_LIMIT_REJECTED",
        }
        raise boundary.M06ScopeDenied(
            known.get(payload["code"], "M06_ACCOUNT_DIAG_API_ERROR")
        )
    boundary.enforce_view_only_permissions(payload)
    from .m06_private_payloads import M06PayloadError
    try:
        parsed = parse_account(payload)
    except (M06PayloadError, ValueError):
        raise boundary.M06ScopeDenied(
            "M06_ACCOUNT_DIAG_BALANCE_SCHEMA_REJECTED"
        ) from None
    # Even a valid account with IDR free balance proves NO bot ownership.
    if parsed.uid <= 0:
        raise boundary.M06ScopeDenied("M06_ACCOUNT_DIAG_UID_INVALID")
    return M06AccountDiagnosticResult()


def run_coverage_readonly_once(*, approval: M06CoverageApproval,
        budget: boundary.M06RequestBudget, api_key: str,
        secret_key: str, local_ownership_db: str | None = None
        ) -> M06CoverageResult:
    """One finite 18-GET session, always REJECT, never retries or orders."""
    if (type(approval) is not M06CoverageApproval
            or type(budget) is not boundary.M06RequestBudget
            or type(api_key) is not str or type(secret_key) is not str
            or local_ownership_db is not None
            and type(local_ownership_db) is not str):
        raise boundary.M06ScopeDenied("M06_COVERAGE_CONTRACT_INVALID")
    now = int(time.time() * 1000)
    if (boundary._RELEASE_APPROVAL_ID != COVERAGE_ID
            or not boundary._NETWORK_RELEASE_GO
            or not approval.issued_ms <= now < approval.expires_ms):
        raise boundary.M06ScopeDenied("M06_COVERAGE_OWNER_RELEASE_GO_ABSENT")
    windows = coverage_windows(approval.start_ms, approval.end_ms)
    if (approval.approval_id != COVERAGE_ID or approval.symbols != PAIRS
            or approval.max_requests != MAX_REQUESTS):
        raise boundary.M06ScopeDenied("M06_COVERAGE_SCOPE_DENIED")
    # Durable burn BEFORE any authenticated GET: no replay after partial results.
    budget.claim_fresh_coverage_once(COVERAGE_ID)
    requests = 0

    def get(path: str, symbol: str | None = None,
            window: tuple[int, int] | None = None):
        nonlocal requests
        ts = int(time.time() * 1000)
        budget.debit(approval, api_key=api_key, now_ms=ts)
        prepared = prepare_signed_get(
            path=path, api_key=api_key, secret_key=secret_key,
            timestamp_ms=ts, symbol=symbol,
            start_ms=window[0] if window else None,
            end_ms=window[1] if window else None,
            limit=1000 if window else None,
        )
        token = boundary._BUDGET_DEBITED_FOR_THIS_GET.set(True)
        try:
            payload = boundary._read_json_over_verified_tls(prepared)
        finally:
            boundary._BUDGET_DEBITED_FOR_THIS_GET.reset(token)
        requests += 1
        return payload

    from .m06_scoped_https import enforce_view_only_permissions
    from .m06_private_payloads import M06PayloadError
    try:
        account_raw = get("/api/v2/account")
        enforce_view_only_permissions(account_raw)
        account = parse_account(account_raw)
        open_orders = boundary._validate_all_pair_orders(get("/api/v2/openOrders"))
        local = None
        if local_ownership_db:
            try:
                local = M06OwnershipLedger.inspect_existing_account_readonly(
                    db_path=local_ownership_db, account_uid=account.uid,
                )
            except OwnershipConflict:
                local = None
        all_orders = 0
        all_fills = 0
        receipt_matches = 0
        receipt_unlinked = 0
        # These are scoped facts, NOT client-supplied authority booleans.
        special_ratios_seen = set()
        local_conflict_free = (
            local is not None
            and not local.local_other_account_records_present
            and not local.local_orphan_fill_records_present
        )
        unresolved = {
            "M06_ACCOUNT_WIDE_HISTORY_NOT_PROVEN",
            "M06_EXTERNAL_PEER_COMMITMENTS_NOT_PROVEN",
            "M06_REAL_BOT_OWNED_LOTS_NOT_PROVEN",
            "M06_SPECIAL_REBUY_SETTLEMENT_NOT_PROVEN",
        }
        if local is None:
            unresolved.add("M06_LOCAL_ACCOUNT_LEDGER_NOT_BOUND")
        for symbol in PAIRS:
            history_rows = []
            fill_rows = []
            for window in windows:
                orders = parse_orders(
                    get("/api/v2/order/histories", symbol, window),
                    expected_symbol=symbol,
                )
                fills = parse_fills(
                    get("/api/v2/myTrades", symbol, window),
                    expected_symbol=symbol,
                )
                if len(orders) >= 1000 or len(fills) >= 1000:
                    raise boundary.M06ScopeDenied(
                        "M06_COVERAGE_HISTORY_POSSIBLY_TRUNCATED"
                    )
                history_rows.extend(orders)
                fill_rows.extend(fills)
            # An order touching the 7-day boundary may appear twice in
            # otherwise complete adjacent windows. Deduplicate only an
            # IDENTICAL record; a conflicting replay is a hard rejection.
            unique_orders = {}
            unique_fills = {}
            for record in history_rows:
                earlier = unique_orders.setdefault(record.order_id, record)
                if earlier != record:
                    raise boundary.M06ScopeDenied("M06_COVERAGE_ORDER_WINDOW_CONFLICT")
            for record in fill_rows:
                earlier = unique_fills.setdefault(record.trade_id, record)
                if earlier != record:
                    raise boundary.M06ScopeDenied("M06_COVERAGE_FILL_WINDOW_CONFLICT")
            history_rows = list(unique_orders.values())
            fill_rows = list(unique_fills.values())
            review = reconcile_readonly_private_snapshot(
                account=account, open_orders=open_orders,
                order_history=tuple(history_rows),
                fills=tuple(fill_rows), symbol=symbol, local_rebuy=local,
            )
            unresolved.update(review.unresolved_requirements)
            # The ratio policy for *both* special assets must have been
            # evaluated against the same authenticated account snapshot.
            if symbol in ("flokiidr", "pepeidr"):
                from .m06_account_review import review_m06_special_partitions
                observed = review_m06_special_partitions(
                    account=account, local_rebuy=local,
                )
                if (len(observed) == 2
                        and all(x.theoretical_trading_cap_qty
                                + x.minimum_protected_qty
                                <= x.observed_wallet_qty for x in observed)):
                    special_ratios_seen.add(symbol)
            all_orders += len(history_rows)
            all_fills += len(fill_rows)
            if local is not None:
                receipt = M06OwnershipLedger.match_exchange_window_to_local_receipts_readonly(
                    db_path=local_ownership_db, account_uid=account.uid,
                    orders=tuple(history_rows), fills=tuple(fill_rows), symbol=symbol,
                )
                receipt_matches += receipt.matched_local_receipts
                receipt_unlinked += receipt.unlinked_fills
            else:
                receipt_unlinked += len(fill_rows)
        # Even after eighteen successful GETs, pair list != all account
        # history, and Indodax cannot attest local/external peer commitments.
        return M06CoverageResult(
            status="M06_COVERAGE_SCOPED_READONLY_REJECT",
            request_count=requests, checked_pairs=len(PAIRS),
            checked_windows_per_pair=len(windows),
            open_orders_count=len(open_orders),
            history_orders_count=all_orders, trade_fills_count=all_fills,
            local_receipts_matched=receipt_matches,
            local_receipts_unlinked=receipt_unlinked,
            unresolved_requirements=tuple(sorted(unresolved)),
            m06_02_balance_and_open_orders_observed=True,
            m06_03_special_pool_ratio_observed=(
                special_ratios_seen == {"flokiidr", "pepeidr"}
            ),
            m06_04_local_fill_receipts_in_window_matched=(
                local is not None
                and receipt_unlinked == 0
                and receipt_matches > 0
            ),
            m06_05_local_account_conflict_free=local_conflict_free,
        )
    except boundary.M06ScopeDenied:
        raise
    except (M06PayloadError, OwnershipConflict, ValueError):
        raise boundary.M06ScopeDenied("M06_COVERAGE_RECONCILIATION_REJECT") from None
