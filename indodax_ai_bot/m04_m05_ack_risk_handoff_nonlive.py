"""Original M05 ACK source-quality -> M04 uncalled role INPUT handoff.

This is strictly a deterministic nonlive safety review of already observed
original M03/WS3 evidence. It never dispatches MARKET/RISK/STRATEGY models,
issues a G1 receipt, promotes a Telegram WATCH, or authorizes trading.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any

from .three_role_ai import ROLES

_ALLOWED_TIERS = frozenset((
    "SOURCE_UNVERIFIED", "PRICE_ONLY", "BOOK_ONLY",
    "TRADE_ONLY", "TRADE_BOOK_OBSERVED",
))
_ALLOWED_TIMELINES = frozenset((
    "UNKNOWN", "AFTER_ALERT_SOURCE_BEFORE_CLAIM",
    "AT_OR_BEFORE_ALERT_SOURCE",
))
_REQUIRED_CAUTIONS = frozenset((
    "G1_INDEPENDENT_SOURCE_UNVERIFIED",
    "COMPLETE_15M_1H_TRADE_TAPE_UNVERIFIED",
    "AGGRESSOR_SIDE_UNVERIFIED",
))
_ROLE_DISPATCH = "NOT_CALLED_G1_UNVERIFIED_OWNER_PAID_GO_ABSENT"


def prepare_m04_m05_ack_research_role_inputs_nonlive(
    *, quality: dict[str, Any],
) -> dict[str, Any]:
    """Strictly validate the output of the original M05 WS3 as-of reviewer.

    All three original M04 role INPUT slots bind to the same immutable LOCAL
    observational digest; NONE represents an actual provider response.
    """
    if not isinstance(quality, dict):
        raise ValueError("M04_ACK_QUALITY_NOT_MAPPING")
    pair = quality.get("pair")
    clock = quality.get("claim_wib")
    tier = quality.get("evidence_tier")
    price = quality.get("price_status")
    micro = quality.get("micro_status")
    timeline = quality.get("micro_vs_alert_source")
    age = quality.get("source_age_sec")
    micro_age = quality.get("micro_age_sec")
    trade = quality.get("fresh_trade_observed")
    book = quality.get("fresh_book_observed")
    cautions = quality.get("research_cautions")
    if (type(pair) is not str or re.fullmatch(r"[a-z0-9]+_idr", pair) is None
            or type(clock) is not str or len(clock) > 40
            or tier not in _ALLOWED_TIERS
            or type(price) is not str
            or price not in ("PRICE_FRESH_AT_CLAIM",
                              "PRICE_SOURCE_NOT_RECONSTRUCTED")
            or type(micro) is not str or not 1 <= len(micro) <= 64
            or re.fullmatch(r"[A-Z0-9_]+", micro) is None
            or timeline not in _ALLOWED_TIMELINES
            or type(age) is not int or age < 0
            or type(trade) is not bool or type(book) is not bool
            or (micro_age is not None and (
                type(micro_age) is not int or micro_age < 0
                or micro_age > 180))
            or type(cautions) is not list or len(cautions) > 15
            or any(type(c) is not str or
                   re.fullmatch(r"[A-Z0-9_]{1,100}", c) is None
                   for c in cautions)
            or len(cautions) != len(set(cautions))
            or not _REQUIRED_CAUTIONS.issubset(cautions)):
        raise ValueError("M04_ACK_QUALITY_SHAPE_INVALID")
    try:
        datetime.fromisoformat(clock)
    except ValueError:
        raise ValueError("M04_ACK_CLAIM_CLOCK_INVALID") from None
    # The original read-only reconstruction is intentionally not a verified
    # issuer receipt, actual AI response, original sender input or order.
    required_fields = {
        "g1_verified": False,
        "m04_actual_role_responses_verified": False,
        "original_m05_sender_consumed_micro_verified": False,
        "source_side_is_not_aggressor_proof": True,
        "watch_publication_policy_changed": False,
        "risk_veto": True,
        "execution_allowed": False,
        "buy_recommendation_allowed": False,
        "final_decision": "REJECT",
        "m04_risk_input_status": "OBSERVATIONAL_SOURCE_REVIEW_ONLY",
        "orders_sent": 0,
        "telegram_messages_sent_by_audit": 0,
        "provider_calls_by_audit": 0,
    }
    if any(type(quality.get(k)) is not type(v) or quality.get(k) != v
           for k, v in required_fields.items()):
        raise ValueError("M04_ACK_UNTRUSTED_AUTHORITY")

    # Assert the tier's claimed observed facts agree with its source flags.
    expected = {
        "SOURCE_UNVERIFIED": (False, False),
        "PRICE_ONLY": (False, False),
        "BOOK_ONLY": (False, True),
        "TRADE_ONLY": (True, False),
        "TRADE_BOOK_OBSERVED": (True, True),
    }
    if ((trade, book) != expected[tier]
            or (tier == "SOURCE_UNVERIFIED") ==
                (price == "PRICE_FRESH_AT_CLAIM")
            or (tier in ("SOURCE_UNVERIFIED", "PRICE_ONLY")
                and (trade or book))
            or (tier != "SOURCE_UNVERIFIED" and age > 150)
            or (tier in ("BOOK_ONLY", "TRADE_ONLY",
                         "TRADE_BOOK_OBSERVED")
                and (micro_age is None or micro_age > 90
                     or timeline == "UNKNOWN"))
            or (micro_age is None and timeline != "UNKNOWN")):
        raise ValueError("M04_ACK_EVIDENCE_TIER_CONFLICT")

    # The packet intentionally omits any invented 1m traded volume, signed
    # BUY pressure, fill/fee, news, or post-claim price. These were NOT in
    # the original bounded source evidence.
    market = {
        "pair": pair,
        "ack_claim_wib": clock,
        "source_exchange_age_at_claim_sec": age,
        "source_price_fresh_at_claim": tier != "SOURCE_UNVERIFIED",
        "evidence_tier": tier,
        "micro_status": micro,
        "micro_age_at_claim_sec": micro_age,
        "micro_relation_to_original_alert_source": timeline,
        "partial_trade_activity_observed": trade,
        "partial_book_snapshot_observed": book,
        "source_caution_codes": list(cautions),
        "rolling_24h_delta_is_executed_1m_volume": False,
        "source_side_is_verified_taker_aggressor": False,
        "closed_candles_verified": False,
        "full_15m_and_1h_trade_tape_verified": False,
        "independent_g1_issuer_verified": False,
        "original_sender_consumption_verified": False,
    }
    digest = sha256(json.dumps(
        market, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")).hexdigest()
    risk_reasons = [
        "INDEPENDENT_G1_ISSUER_AND_COMPLETE_TAPE_MISSING",
        "ACTUAL_THREE_ROLE_PROVIDER_RESPONSES_NOT_OBSERVED",
        "ACCOUNT_FEES_SLIPPAGE_AND_OWNERSHIP_NOT_VERIFIED",
    ]
    if tier == "SOURCE_UNVERIFIED":
        risk_reasons.append("ASOF_PRICE_SOURCE_NOT_VERIFIED")
    if tier == "PRICE_ONLY":
        risk_reasons.append("MICRO_TRADE_AND_BOOK_UNKNOWN")
    if tier == "BOOK_ONLY":
        risk_reasons.append("MICRO_TRADE_UNKNOWN")
    if tier == "TRADE_ONLY":
        risk_reasons.append("MICRO_ORDERBOOK_UNKNOWN")
    if timeline == "AFTER_ALERT_SOURCE_BEFORE_CLAIM":
        risk_reasons.append("MICRO_NOT_AVAILABLE_AT_ALERT_SOURCE_EPOCH")
    for code in (
        "OBSERVED_SPREAD_OVER_50BPS_RESEARCH",
        "OBSERVED_BID_DEPTH_BELOW_1M_RESEARCH",
        "SOURCE_REPORTED_SELL_SIDE_HIGH_NOT_AGGRESSOR_PROOF",
        "MICRO_TIMELINE_UNTRUSTED",
        "MICRO_CAPTURE_STALE",
    ):
        if code in cautions:
            risk_reasons.append(code)

    return {
        "stage": "M04_ORIGINAL_M05_ACK_ASOF_REVIEW_ONLY",
        "status": "UNATTESTED_M04_INPUT_SLOTS_PREPARED_REJECT_ONLY",
        "pair": pair,
        "evidence_digest_sha256": digest,
        "market_public_observations": market,
        "deterministic_risk_veto": {
            "veto": True,
            "reasons": risk_reasons,
            "confidence_in_positive_trade_edge": "UNVERIFIED",
            "risk_review_is_real_ai_response": False,
        },
        "deterministic_strategy": {
            "stance": "WATCH_NO_TRADE",
            "net_profit_10000_idr_verified": False,
            "net_roi_2_percent_verified": False,
            "risk_veto_overridden": False,
            "strategy_review_is_real_ai_response": False,
        },
        "role_slots": [{
            "role": role,
            "source_digest_sha256": digest,
            "dispatch": _ROLE_DISPATCH,
            "actual_model_response": None,
            "required_prior_roles": list(ROLES[:idx]),
        } for idx, role in enumerate(ROLES)],
        "role_slots_prepared": len(ROLES),
        "independent_g1_issuer_verified": False,
        "actual_three_model_responses_observed": False,
        "paid_ai_owner_go_authorized": False,
        "provider_http_attempts": 0,
        "owner_order_specific_final_verified": False,
        "final_decision": "REJECT",
        "execution_allowed": False,
        "orders_sent": 0,
        "telegram_messages_sent": 0,
    }
