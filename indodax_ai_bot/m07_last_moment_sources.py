"""M07-03 original last-moment evidence join; read-only and HARDLOCKED.

Never promote a public G1-less candidate, a View Only M06 balance snapshot,
or an M05 owner advisory PASS into an executable trade. This intentionally
does not invent exchange orders or mutable execution authority.
"""
from __future__ import annotations
from decimal import Decimal
from typing import Any

from .m07_limit_preflight import M07LimitBuyPreflightPreview


def review_m07_last_moment_sources_offline(*,
        limit_preview: M07LimitBuyPreflightPreview,
        public_candidate: dict[str, Any] | None,
        account_snapshot: dict[str, Any] | None,
        owner_advisory: dict[str, Any] | None,
        offline_intent_guard: dict[str, Any],
        now_epoch: int,
) -> dict[str, Any]:
    """Evidence map from existing source outputs, no new source trust.

    View Only actual free IDR can be observed WITHOUT being entirely
    spendable for MAIN because FLOKI/PEPE proceeds and external commitments
    remain unknown. Actual owner-button PASS for a G1-unqualified *advisory*
    can be observed WITHOUT authorizing the exact order draft.
    """
    if (type(limit_preview) is not M07LimitBuyPreflightPreview
            or type(now_epoch) is not int or now_epoch <= 0
            or type(offline_intent_guard) is not dict
            or offline_intent_guard.get("stage") !=
               "M07_ORIGINAL_OFFLINE_INTENT_GUARD"):
        raise ValueError("M07_LAST_MOMENT_INPUT_INVALID")
    pair = limit_preview.symbol[:-3].lower() + "_idr"
    p = public_candidate if type(public_candidate) is dict else {}
    m = account_snapshot if type(account_snapshot) is dict else {}
    o = owner_advisory if type(owner_advisory) is dict else {}
    public_bound = (
        p.get("stage") == "M07_SAME_CANDIDATE_PUBLIC_PREFLIGHT"
        and p.get("candidate_pair") == pair
        and p.get("observed_pair") == pair
        and p.get("same_candidate_and_closed_minute") is True
        and p.get("status") == "M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY"
        and p.get("execution_allowed") is False
        and type(p.get("observed_epoch")) is int
        and 0 <= now_epoch - p["observed_epoch"] <= 30
    )
    account_observed = (
        m.get("stage") == "M06_ACTUAL_IDR_VIEWONLY"
        and m.get("status") ==
            "M06_IDR_BALANCE_OBSERVED_SOURCE_RESERVE_UNVERIFIED"
        and m.get("view_only_permissions_verified") is True
        and m.get("requests_completed") == 2
        and m.get("execution_allowed") is False
    )
    special_excluded = (
        account_observed
        and m.get("all_special_sell_history_verified") is True
        and m.get("local_special_ledger_uid_bound") is True
        and m.get("local_rebuy_receipts_linked") is True
    )
    real_spendable = (
        account_observed and special_excluded
        and m.get("all_peer_liabilities_verified") is True
        and m.get("spendable_idr_verified") is True
        and m.get("source_authenticated") is True
    )
    # Current M06 does not produce the above COMPLETE flags, and this join
    # explicitly does NOT endorse a forged caller-provided True. The input
    # map is only an observed diagnostic, never independent attestation.
    advisory_pass = (
        o.get("stage") == "M05_SOURCE_BOUND_ADVISORY_READONLY"
        and o.get("status") == "M05_OWNER_BUTTON_PASS_ADVISORY_ONLY"
        and o.get("source_bound_owner_review_pass") is True
        and o.get("owner_order_execution_consent_verified") is False
        and o.get("execution_allowed") is False
    )
    unresolved_count = offline_intent_guard.get("unresolved_simulated_intents")
    unknown_count = offline_intent_guard.get("offline_unknown_holds")
    journal_blocked = (
        type(unresolved_count) is not int or unresolved_count != 0
        or type(unknown_count) is not int or unknown_count != 0
        or offline_intent_guard.get("additional_buy_reservations_blocked") is not False
    )
    missing = []
    if not public_bound:
        missing.append("M07_PUBLIC_PAIR_OR_CANDLE_NOT_FRESH")
    if not limit_preview.checks_passed_unverified:
        missing.append("M07_LIMIT_ORDER_RULES_NOT_MET")
    if not account_observed:
        missing.append("M06_EXCHANGE_ACCOUNT_NOT_JOINED")
    if not special_excluded:
        missing.append("M06_FLOKI_PEPE_PROCEEDS_NOT_COMPLETE")
    if not real_spendable:
        missing.append("M06_SPENDABLE_IDR_NOT_AUTHENTICATED")
    if not advisory_pass:
        missing.append("M05_SAME_SOURCE_OWNER_REVIEW_NOT_PASS")
    if journal_blocked:
        missing.append("M07_LOCAL_OUTSTANDING_OR_UNKNOWN_INTENT")
    missing += [
        "G1_RISK_ROLE_SOURCE_NOT_ATTESTED",
        "M05_ADVISORY_PASS_NOT_ORDER_AUTHORIZATION",
        "M07_REAL_EXCHANGE_INTENT_RECONCILIATION_REQUIRED",
        "M07_ORDER_EXECUTION_HARDLOCKED",
    ]
    return {
        "stage": "M07_ORIGINAL_LAST_MOMENT_SOURCE_GATE_OFFLINE",
        "status": "BLOCKED_UNVERIFIED_FULL_ORDER_PRECONDITIONS",
        "candidate_pair": pair,
        "public_same_candidate_fresh_observed": public_bound,
        "limit_math_only_passed": limit_preview.checks_passed_unverified,
        "m06_viewonly_account_observed": account_observed,
        "m06_report_claims_complete_special_history": special_excluded,
        "m06_report_claims_spendable_verified": real_spendable,
        "m06_complete_account_independently_attested": False,
        "m06_general_idr_order_budget_authorized": False,
        "m05_same_source_advisory_review_pass": advisory_pass,
        "m05_order_specific_final_verified": False,
        "m07_unresolved_or_unknown_local_intent": journal_blocked,
        "m07_exchange_unknown_reconciliation_verified": False,
        "missing_requirements": missing,
        "order_sent": False,
        "execution_allowed": False,
    }
