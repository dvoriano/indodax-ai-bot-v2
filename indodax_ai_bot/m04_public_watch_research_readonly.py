"""Original V2 PUBLIC WATCH research contract, deterministic/offline only.

Consume ONE existing M03->M04 source-bound handoff (max 2 watches) and
produce three explicitly NON-AI research views for the SAME public evidence.
This is NOT a replacement for ThreeRoleShadow, a qualified trading
proposal, a live model call, an internal G1 issuer, or a Telegram sender.

No new scanner, market HTTP/WS request, provider, account GET, SQLite
write, background worker, Telegram notification or exchange order.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any

from .m04_radar_three_role_handoff_readonly import (
    _MISSING_FACTS, _ROLE_DISPATCH,
    inspect_original_radar_three_role_handoff_readonly,
)
from .m05_radar_momentum_nonlive import DEFAULT_DB
from .three_role_ai import ROLES

_PAIR = re.compile(r"^[a-z0-9]+_idr$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_STATUS = "M04_PUBLIC_WATCH_RESEARCH_OFFLINE_ONLY"
_KIND = "DETERMINISTIC_SOURCE_INSPECTION_NOT_AN_AI_RESPONSE"
_OBSERVATION_KEYS = frozenset((
    "source_kind", "pair", "observed_epoch", "reference_epoch",
    "reference_interval_seconds", "price_snapshot_before_idr",
    "price_snapshot_after_idr", "observed_rise_pct",
    "volume_24h_snapshot_idr", "volume_24h_rolling_net_delta_idr",
    "volume_24h_rolling_net_delta_pct",
    "rolling_delta_is_closed_1m_volume",
    "spread_snapshot_bps", "missing_intermediate_minute_samples",
    "watch_type", "pre_ai_review_priority",
    "public_sample_fingerprint_sha256",
    "source_is_actual_closed_candle", "independent_g1_issuer_verified",
    "complete_trade_tape_verified", "maintenance_status", "news_status",
))
_PRIORITY = {
    1: "PRICE_AND_ROLLING_DELTA_REVIEW",
    2: "PRICE_MOMENTUM_REVIEW",
    3: "ROLLING_DELTA_PRICE_WEAK_REVIEW",
    4: "ACTIVITY_ONLY_PRICE_NOT_RISING",
}


def _result(status: str) -> dict[str, Any]:
    return {
        "stage": "M04_ORIGINAL_PUBLIC_WATCH_RESEARCH",
        "status": status,
        "scope": "READ_ONLY_EXISTING_ORIGINAL_M03_SOURCE",
        "research_kind": _KIND,
        "candidate_count": 0,
        "candidates": [],
        "original_m03_inspections": 0,
        "original_m05_receiver_modified": False,
        "provider_http_calls": 0,
        "actual_market_ai_responses": 0,
        "actual_risk_ai_responses": 0,
        "actual_strategy_ai_responses": 0,
        "three_real_role_responses_observed": False,
        "independent_g1_issuer_verified": False,
        "paid_ai_go_authorized": False,
        "telegram_messages_sent": 0,
        "owner_final_decision": None,
        "recommendation_to_buy": False,
        "order_sent": False,
        "execution_allowed": False,
    }


def _decimal(value: Any, *, positive: bool = False) -> Decimal:
    if type(value) is not str or not 1 <= len(value) <= 64:
        raise ValueError("INVALID_PUBLIC_NUMBER")
    try:
        d = Decimal(value)
    except InvalidOperation:
        raise ValueError("INVALID_PUBLIC_NUMBER") from None
    if (not d.is_finite() or abs(d) > Decimal("1e20")
            or (d <= 0 if positive else d < 0)):
        raise ValueError("INVALID_PUBLIC_NUMBER")
    return d


def _research_candidate(row: dict, *, now_epoch: int) -> dict[str, Any]:
    if (not isinstance(row, dict) or type(row.get("pair")) is not str
            or not _PAIR.fullmatch(row["pair"])
            or type(row.get("evidence_digest_sha256")) is not str
            or not _HEX.fullmatch(row["evidence_digest_sha256"])
            or row.get("owner_order_specific_consent_verified") is not False
            or row.get("execution_allowed") is not False
            or row.get("required_net_profit_idr") != "10000"
            or row.get("required_net_roi_percent") != "2"
            or row.get("required_threshold_relation") != "AND"
            or row.get("deterministic_pre_ai_risk_status") !=
                "NO_TRADE_SOURCE_AND_ACCOUNT_UNVERIFIED"
            or row.get("deterministic_pre_ai_strategy_status") !=
                "WAIT_NO_AUTHENTICATED_ENTRY_AND_NET_COSTS"
            or row.get("source_missing") != list(_MISSING_FACTS)):
        raise ValueError("PUBLIC_WATCH_ROLE_SOURCE_UNVERIFIED")

    packet = row.get("public_evidence")
    if not isinstance(packet, dict) or set(packet) != _OBSERVATION_KEYS:
        raise ValueError("PUBLIC_WATCH_PACKET_FIELDS_UNVERIFIED")
    if (packet["source_kind"] !=
            "ORIGINAL_M03_PUBLIC_TICKER_WATCH_UNATTESTED"
            or packet["pair"] != row["pair"]
            or type(packet["observed_epoch"]) is not int
            or type(packet["reference_epoch"]) is not int
            or type(packet["reference_interval_seconds"]) is not int
            or not 0 < packet["reference_epoch"] < packet["observed_epoch"]
            or not 0 <= now_epoch - packet["observed_epoch"] <= 90
            # The original window span is measured from minute buckets,
            # while these two epoch fields are ACTUAL observation times.
            # Their within-minute offsets can differ by up to 59s.
            or abs(packet["observed_epoch"] - packet["reference_epoch"] -
                   packet["reference_interval_seconds"]) > 60
            or not 60 <= packet["reference_interval_seconds"] <= 300
            or packet["rolling_delta_is_closed_1m_volume"] is not False
            or packet["source_is_actual_closed_candle"] is not False
            or packet["independent_g1_issuer_verified"] is not False
            or packet["complete_trade_tape_verified"] is not False
            or packet["maintenance_status"] != "UNVERIFIED"
            or packet["news_status"] != "NEWS_UNAVAILABLE"
            or type(packet["missing_intermediate_minute_samples"]) is not bool
            or type(packet["public_sample_fingerprint_sha256"]) is not str
            or not _HEX.fullmatch(packet["public_sample_fingerprint_sha256"])):
        raise ValueError("PUBLIC_WATCH_PACKET_SOURCE_OR_FRESHNESS_DENIED")

    canonical = json.dumps(
        packet, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("ascii")
    if sha256(canonical).hexdigest() != row["evidence_digest_sha256"]:
        raise ValueError("PUBLIC_WATCH_PACKET_DIGEST_MISMATCH")
    slots = row.get("role_slots")
    if (not isinstance(slots, list) or len(slots) != 3
            or any(not isinstance(slot, dict) or set(slot) != {
                "role", "evidence_digest_sha256", "dispatch", "model_output"
            } for slot in slots)
            or [slot["role"] for slot in slots] != list(ROLES)
            or any(
                slot["evidence_digest_sha256"] != row["evidence_digest_sha256"]
                or slot["dispatch"] != _ROLE_DISPATCH
                or slot["model_output"] is not None
                for slot in slots
            )):
        raise ValueError("PUBLIC_WATCH_ROLE_SOURCE_OR_DISPATCH_MISMATCH")

    before = _decimal(packet["price_snapshot_before_idr"], positive=True)
    after = _decimal(packet["price_snapshot_after_idr"], positive=True)
    pct = Decimal(packet["observed_rise_pct"])
    if not pct.is_finite() or abs(
            (after / before - 1) * 100 - pct) > Decimal("0.02"):
        raise ValueError("PUBLIC_WATCH_RESEARCH_PRICE_MISMATCH")
    vol = _decimal(packet["volume_24h_snapshot_idr"], positive=True)
    spread = _decimal(packet["spread_snapshot_bps"])
    if vol < Decimal("150000000") or spread > Decimal("100"):
        raise ValueError("PUBLIC_WATCH_RESEARCH_LIQUIDITY_MISMATCH")
    watch = packet["watch_type"]
    if watch not in (
        "PRICE_MOMENTUM_WATCH", "ROLLING_TURNOVER_CHANGE_WATCH",
        "PRICE_AND_ROLLING_TURNOVER_WATCH",
    ):
        raise ValueError("PUBLIC_WATCH_RESEARCH_UNKNOWN_WATCH_TYPE")
    has_rolling = watch != "PRICE_MOMENTUM_WATCH"
    if has_rolling:
        if (_decimal(packet["volume_24h_rolling_net_delta_idr"]) <= 0
                or _decimal(packet["volume_24h_rolling_net_delta_pct"]) <= 0):
            raise ValueError("PUBLIC_WATCH_RESEARCH_ROLLING_DELTA_INVALID")
    elif (packet["volume_24h_rolling_net_delta_idr"] is not None
          or packet["volume_24h_rolling_net_delta_pct"] is not None):
        raise ValueError("PUBLIC_WATCH_RESEARCH_UNEXPECTED_ROLLING_DELTA")

    expected_priority = (
        4 if pct <= 0 else
        1 if watch == "PRICE_AND_ROLLING_TURNOVER_WATCH" else
        2 if watch == "PRICE_MOMENTUM_WATCH" else 3
    )
    if (type(row.get("pre_ai_priority_rank_observational")) is not int
            or row["pre_ai_priority_rank_observational"] != expected_priority
            or packet["pre_ai_review_priority"] != _PRIORITY[expected_priority]
            or row.get("deterministic_pre_ai_market_priority") !=
                _PRIORITY[expected_priority]
            or type(row.get("radar_original_top5_rank")) is not int
            or not 1 <= row["radar_original_top5_rank"] <= 5
            or type(row.get("handoff_selection_rank")) is not int
            or not 1 <= row["handoff_selection_rank"] <= 2):
        raise ValueError("PUBLIC_WATCH_RESEARCH_PRIORITY_MISMATCH")

    if pct < 0:
        market_view = "ROLLING_VOLUME_DELTA_WITH_FALLING_QUOTE"
    elif pct == 0:
        market_view = "ROLLING_VOLUME_DELTA_WITH_FLAT_QUOTE"
    elif watch == "PRICE_AND_ROLLING_TURNOVER_WATCH":
        market_view = "RISING_QUOTE_AND_ROLLING_24H_DELTA"
    elif watch == "PRICE_MOMENTUM_WATCH":
        market_view = "RISING_QUOTE_WATCH"
    else:
        market_view = "RISING_QUOTE_WEAK_ROLLING_DELTA_WATCH"

    cautions = list(_MISSING_FACTS)
    if packet["missing_intermediate_minute_samples"]:
        cautions.append("MISSING_INTERMEDIATE_SOURCE_MINUTE")
    if spread > Decimal("60"):
        cautions.append("OBSERVED_SPREAD_OVER_60_BPS")
    if pct <= 0:
        cautions.append("PRICE_NOT_RISING_NO_BUY_PRESSURE_INFERENCE")

    digest = row["evidence_digest_sha256"]
    role_views = [
        {
            "role": "MARKET",
            "kind": _KIND,
            "evidence_digest_sha256": digest,
            "observational_label": market_view,
            "price_change_percent_sampled": str(pct),
            "rolling_24h_delta_is_1m_executed_volume": False,
            "model_invoked": False,
        },
        {
            "role": "RISK",
            "kind": _KIND,
            "evidence_digest_sha256": digest,
            "status": "PUBLIC_RESEARCH_ONLY_NO_TRADE",
            "caution_codes": cautions,
            "model_invoked": False,
        },
        {
            "role": "STRATEGY",
            "kind": _KIND,
            "evidence_digest_sha256": digest,
            "status": "WAIT_NO_VERIFIED_ENTRY_FEES_AND_CAPITAL",
            "net_profit_idr": None,
            "net_roi_percent": None,
            "minimum_net_profit_idr": "10000",
            "minimum_net_roi_percent": "2",
            "minimum_targets_relation": "AND",
            "model_invoked": False,
        },
    ]
    return {
        "pair": row["pair"],
        "evidence_digest_sha256": digest,
        "source_status": "PUBLIC_WATCH_UNATTESTED",
        "sample_observed_epoch": packet["observed_epoch"],
        "sample_age_seconds": now_epoch - packet["observed_epoch"],
        "original_radar_rank": row["radar_original_top5_rank"],
        "research_selection_rank": row["handoff_selection_rank"],
        "role_research_views": role_views,
        "required_missing_evidence": list(_MISSING_FACTS),
        "all_role_views_use_same_digest": True,
        "g1_receipt_attested": False,
        "actual_ai_role_calls": 0,
        "trade_proposal_created": False,
        "owner_final_decision": None,
        "execution_allowed": False,
    }


def prepare_public_watch_research_readonly(
    *, handoff: dict, now_epoch: int,
) -> dict[str, Any]:
    """Validate a FULL original handoff cohort; don't promote public evidence."""
    if type(now_epoch) is not int or now_epoch <= 0:
        return _result("M04_PUBLIC_WATCH_RESEARCH_INVALID_CLOCK")
    if (not isinstance(handoff, dict)
            or handoff.get("status") not in (
                "M04_RADAR_HANDOFF_PUBLIC_PACKETS_PREPARED",
                "M04_RADAR_HANDOFF_NO_CURRENT_WATCH",
            )
            or handoff.get("g1_independent_issuer_verified") is not False
            or handoff.get("three_real_role_responses_observed") is not False
            or handoff.get("paid_ai_go_authorized") is not False
            or handoff.get("execution_allowed") is not False
            or handoff.get("provider_http_calls") != 0
            or handoff.get("telegram_messages_sent") != 0
            or handoff.get("order_sent") is not False
            or handoff.get("source_digest_is_g1_receipt") is not False
            or handoff.get("selection_policy") !=
                "VALIDATE_ALL_ORIGINAL_TOP5_THEN_PRE_AI_PRIORITY_ASC_TIE_ORIGINAL_RADAR_RANK"
            or type(handoff.get("candidate_count")) is not int
            or not 0 <= handoff["candidate_count"] <= 2
            or not isinstance(handoff.get("candidates"), list)
            or len(handoff["candidates"]) != handoff["candidate_count"]
            or handoff["status"] == "M04_RADAR_HANDOFF_NO_CURRENT_WATCH"
            and handoff["candidate_count"] != 0
            or handoff["status"] == "M04_RADAR_HANDOFF_PUBLIC_PACKETS_PREPARED"
            and handoff["candidate_count"] == 0):
        return _result("M04_PUBLIC_WATCH_RESEARCH_HANDOFF_UNVERIFIED")

    result = _result(
        _STATUS if handoff["candidate_count"] else
        "M04_PUBLIC_WATCH_RESEARCH_NO_CURRENT_CANDIDATE"
    )
    # A pure provided snapshot is not independently an actual DB read.
    # Only the wrapper that invokes the original inspector can attest one.
    prepared = []
    seen_pairs: set[str] = set()
    for index, row in enumerate(handoff["candidates"], start=1):
        try:
            candidate = _research_candidate(row, now_epoch=now_epoch)
            if (candidate["pair"] in seen_pairs
                    or candidate["research_selection_rank"] != index):
                raise ValueError("PUBLIC_WATCH_RESEARCH_DUPLICATE_OR_UNORDERED")
            seen_pairs.add(candidate["pair"])
        except (ValueError, TypeError, KeyError, InvalidOperation,
                UnicodeError, OverflowError):
            return _result("M04_PUBLIC_WATCH_RESEARCH_SOURCE_REJECTED")
        prepared.append(candidate)
    result["candidate_count"] = len(prepared)
    result["candidates"] = prepared
    return result


def inspect_original_public_watch_research_readonly(
    *, db_path: Path = DEFAULT_DB, now_epoch: int | None = None,
) -> dict[str, Any]:
    """ONE existing original inspector call, never new market fetches."""
    now = int(time.time()) if now_epoch is None else now_epoch
    if type(now) is not int or now <= 0:
        return _result("M04_PUBLIC_WATCH_RESEARCH_INVALID_CLOCK")
    handoff = inspect_original_radar_three_role_handoff_readonly(
        now_epoch=now, db_path=db_path,
    )
    result = prepare_public_watch_research_readonly(
        handoff=handoff, now_epoch=now,
    )
    result["original_m03_inspections"] = 1
    return result


def main() -> int:
    report = inspect_original_public_watch_research_readonly()
    # No token, owner ID, API URL, private account or Telegram message.
    print(json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
