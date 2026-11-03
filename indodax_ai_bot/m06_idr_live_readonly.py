"""Owner-triggered M06 true exchange IDR balance + open orders + local rebuy.

READ ONLY: exactly two signed TAPI V2 GETs (account, all-pair openOrders),
max one attempt per endpoint, no POST/DELETE or automatic timer. The actual
Indodax account values are not a bot trading authorization; local special
rebuy source completeness cannot be inferred from present ledger amounts.
"""
from __future__ import annotations

from decimal import Decimal
import time

from .m06_private_payloads import parse_account, M06PayloadError
from .m06_scoped_https import (
    enforce_view_only_permissions, _validate_all_pair_orders, M06ScopeDenied,
)
from .m06_signed_readonly import prepare_signed_get
from .m06_ownership_ledger import M06OwnershipLedger, OwnershipConflict
from .policy import ScalpingPolicy
from .m07_private_order_readonly import _https_json, M07ReadOnlyError


def inspect_idr_budget_readonly_once(*, api_key: str, secret_key: str,
                                     local_ownership_db: str | None) -> dict:
    """Report observed free IDR and conservative source-tagged preview.

    Missing or insecure UID-bound existing M06 ledger -> no spendable number.
    Existing local special records still do NOT establish complete history.
    All output is redacted of UID, secret, client/order IDs and raw payloads.
    """
    if (type(api_key) is not str or not api_key or
            type(secret_key) is not str or not secret_key
            or local_ownership_db is not None and
            (type(local_ownership_db) is not str or not local_ownership_db)):
        raise M06ScopeDenied("M06_IDR_READONLY_INPUT_INVALID")
    def get(path: str):
        try:
            request = prepare_signed_get(
                path=path, api_key=api_key, secret_key=secret_key,
                timestamp_ms=int(time.time()*1000),
            )
            return _https_json(request)
        except (ValueError, M07ReadOnlyError, OSError):
            raise M06ScopeDenied("M06_IDR_READONLY_GET_FAILED") from None

    raw = get("/api/v2/account")
    if type(raw) is dict and type(raw.get("code")) is int:
        raise M06ScopeDenied("M06_IDR_ACCOUNT_API_REJECTED")
    enforce_view_only_permissions(raw)
    try:
        account = parse_account(raw)
    except (M06PayloadError, ValueError):
        raise M06ScopeDenied("M06_IDR_ACCOUNT_SCHEMA_REJECTED") from None
    # All-pair openOrders is queried without symbol (not a single trading
    # shortlist). A non-IDR pair, duplicate, 1000-row cap or malformed full ID
    # causes safe UNRESOLVED; cannot silently ignore a possible IDR liability.
    orders_raw = get("/api/v2/openOrders")
    if type(orders_raw) is dict and type(orders_raw.get("code")) is int:
        raise M06ScopeDenied("M06_IDR_OPEN_ORDERS_API_REJECTED")
    try:
        orders = _validate_all_pair_orders(orders_raw)
    except (M06ScopeDenied, M06PayloadError, ValueError):
        raise M06ScopeDenied("M06_IDR_OPEN_ORDERS_SCOPE_UNRESOLVED") from None

    result = {
        "status": "M06_IDR_BALANCE_OBSERVED_SOURCE_RESERVE_UNVERIFIED",
        "source_currency": "IDR",
        "requests_completed": 2,
        "account_observed_epoch": int(time.time()),
        "view_only_permissions_verified": True,
        "exchange_idr_free_observed": str(account.free_idr),
        "exchange_idr_locked_observed": str(account.locked_idr),
        "all_pair_open_orders_observed": len(orders),
        "local_special_ledger_uid_bound": False,
        "local_shadow_coverage_complete": False,
        "local_rebuy_receipts_linked": False,
        "local_external_peer_coverage_verified": False,
        "floki_rebuy_local_hold_idr": None,
        "pepe_rebuy_local_hold_idr": None,
        "other_local_not_locked_commitments_idr": None,
        "general_idr_unverified_preview": None,
        "source_authenticated": False,
        "all_special_sell_history_verified": False,
        "all_peer_liabilities_verified": False,
        "spendable_idr_verified": False,
        "execution_allowed": False,
        "order_sent": False,
    }
    if local_ownership_db is None:
        result["missing_evidence"] = [
            "M06_LOCAL_REBUY_LEDGER_PATH_NOT_GIVEN",
            "M06_SPECIAL_SELL_PROCEEDS_COMPLETE_HISTORY_UNPROVEN",
            "M06_EXTERNAL_ACCOUNT_COMMITMENTS_UNPROVEN",
        ]
        return result
    try:
        local = M06OwnershipLedger.inspect_existing_account_readonly(
            db_path=local_ownership_db, account_uid=account.uid,
        )
    except OwnershipConflict:
        result["missing_evidence"] = [
            "M06_LOCAL_UID_BOUND_LEDGER_NOT_VERIFIED",
            "M06_SPECIAL_SELL_PROCEEDS_COMPLETE_HISTORY_UNPROVEN",
            "M06_EXTERNAL_ACCOUNT_COMMITMENTS_UNPROVEN",
        ]
        return result
    # This is a locally observed floor, not a proof the FULL current
    # special-asset history was captured. It MUST never authorize an order.
    result["local_special_ledger_uid_bound"] = True
    result["local_rebuy_receipts_linked"] = local.local_rebuy_receipts_linked
    result["local_shadow_coverage_complete"] = local.local_shadow_coverage_complete
    result["floki_rebuy_local_hold_idr"] = str(local.floki_idr_local_protected)
    result["pepe_rebuy_local_hold_idr"] = str(local.pepe_idr_local_protected)
    if local.local_shadow_coverage_complete:
        result["other_local_not_locked_commitments_idr"] = str(
            local.local_shadow_commitments_idr
        )
        # Show only a PREVIEW; this result is never registered in the
        # live capital authority and cannot unlock any new BUY.
        review = ScalpingPolicy().review_main_capital(
            free_idr=account.free_idr, locked_idr=account.locked_idr,
            floki_rebuy_idr=local.floki_idr_local_protected,
            pepe_rebuy_idr=local.pepe_idr_local_protected,
            commitments_not_locked_idr=local.local_shadow_commitments_idr,
        )
        result["general_idr_unverified_preview"] = str(review.spendable_idr)
        result["observed_known_commitments_overdraw_free_idr"] = review.overcommitted
    missing = ["M06_SPECIAL_SELL_PROCEEDS_COMPLETE_HISTORY_UNPROVEN",
               "M06_EXTERNAL_ACCOUNT_COMMITMENTS_UNPROVEN"]
    if not local.local_rebuy_receipts_linked:
        missing.append("M06_LOCAL_REBUY_RECEIPTS_UNLINKED")
    if not local.local_shadow_coverage_complete:
        missing.append("M06_LOCAL_SHADOW_COVERAGE_INCOMPLETE")
    if local.local_other_account_records_present:
        missing.append("M06_FOREIGN_LOCAL_ACCOUNT_RECORDS_PRESENT")
    if local.local_orphan_fill_records_present:
        missing.append("M06_LOCAL_ORPHAN_FILL_RECORDS_PRESENT")
    result["missing_evidence"] = missing
    return result
