"""Original M03 radar -> M04 three-role EVIDENCE HANDOFF, read-only and OFFLINE.

This bridge does NOT call MARKET/RISK/STRATEGY models. It prepares up to two
source-bound PUBLIC WATCH packets using the already-running original M03
minute ticker SQLite through the existing inspector. A local digest is NOT
a G1 issuer attestation. The existing ThreeRoleShadow/real Sumopod caller
remains disconnected until authenticated G1, verified role prerequisites
and a separate owner-paid-AI GO are available.

No new market scanner, REST/WS, Telegram poller/sender, DB write, paid
request, decision or exchange order is created here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any

from .m05_radar_momentum_nonlive import (
    DEFAULT_DB, MAX_AGE_SECONDS, MIN_TURNOVER_IDR,
    MAX_SPREAD_BPS, PAIR_ID, classify_public_watch_pre_ai_nonlive,
    inspect_original_m03_radar_readonly,
)
from .scanner import MAIN_EXCLUDED_PAIRS
from .m03_solo_idr_cycle_policy import (
    SoloIDRPolicyInvalid, candidate_quality_watch,
)
from .three_role_ai import ROLES


_SAFE_WATCH_TYPES = frozenset((
    "PRICE_MOMENTUM_WATCH",
    "ROLLING_TURNOVER_CHANGE_WATCH",
    "PRICE_AND_ROLLING_TURNOVER_WATCH",
))
_MISSING_FACTS = (
    "closed_1m_5m_15m_candles",
    "volume_of_closed_1m_candle",
    "complete_public_trade_tape",
    "verified_buy_sell_aggressor_flow",
    "multi_level_order_book_depth",
    "maintenance_and_listing_status",
    "timestamped_news_and_unlocks",
    "owner_available_idr_and_commitments",
    "owned_position_cost_basis",
    "verified_all_in_fees_tax_and_slippage",
    "independent_g1_issuer_receipt",
)
_ROLE_DISPATCH = "NOT_CALLED_G1_UNVERIFIED_AND_OWNER_PAID_AI_GO_ABSENT"


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "stage": "M04_ORIGINAL_M03_THREE_ROLE_HANDOFF_READONLY",
        "status": reason,
        "source": "EXISTING_ORIGINAL_M03_TICKER_INSPECTOR_ONLY",
        "candidate_count": 0,
        "candidates": [],
        "roles_configured_locally_verified": False,
        "g1_independent_issuer_verified": False,
        "three_real_role_responses_observed": False,
        "paid_ai_go_authorized": False,
        "provider_http_calls": 0,
        "telegram_messages_sent": 0,
        "new_scanner_calls": 0,
        "owner_final_decision": None,
        "order_sent": False,
        "execution_allowed": False,
    }


def _positive_decimal(value: Any, *, allow_zero: bool = False) -> Decimal:
    if type(value) is not str or not 1 <= len(value) <= 64:
        raise ValueError("M04_RADAR_INVALID_NUMERIC_SOURCE")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("M04_RADAR_INVALID_NUMERIC_SOURCE") from None
    if (not number.is_finite()
            or (number < 0 if allow_zero else number <= 0)
            or number.adjusted() > 20):
        raise ValueError("M04_RADAR_INVALID_NUMERIC_SOURCE")
    return number


def _source_packet(candidate: dict, now_epoch: int) -> dict[str, Any]:
    """Whitelist ONLY genuine original ticker observations and gap warnings."""
    if not isinstance(candidate, dict):
        raise ValueError("M04_RADAR_INVALID_CANDIDATE")
    pair = candidate.get("pair")
    observed = candidate.get("observed_epoch")
    reference = candidate.get("reference_epoch")
    age = candidate.get("sample_age_seconds")
    span = candidate.get("reference_interval_seconds")
    if (type(pair) is not str or not PAIR_ID.fullmatch(pair)
            or pair in MAIN_EXCLUDED_PAIRS
            or type(observed) is not int or not 0 < observed <= now_epoch
            or type(reference) is not int or not 0 < reference < observed
            or type(age) is not int or age != now_epoch - observed
            or age > MAX_AGE_SECONDS
            or type(span) is not int or not 60 <= span <= 300
            or observed - reference < 60 or observed - reference > 360
            or type(candidate.get("price_watch")) is not bool
            or type(candidate.get("volume_watch")) is not bool
            or not (candidate["price_watch"] or candidate["volume_watch"])
            or type(candidate.get("missing_intermediate_minute_samples")) is not bool
            or type(candidate.get("casebook_early_watch_observed")) is not bool
            or candidate.get("rolling_24h_turnover_not_1m_traded_volume") is not True
            or candidate.get("no_g1_or_ai_or_order_authority") is not True
            or candidate.get("watch_type") not in _SAFE_WATCH_TYPES):
        raise ValueError("M04_RADAR_UNTRUSTED_PUBLIC_WATCH")

    expected_type = (
        "PRICE_AND_ROLLING_TURNOVER_WATCH"
        if candidate["price_watch"] and candidate["volume_watch"]
        else "PRICE_MOMENTUM_WATCH" if candidate["price_watch"]
        else "ROLLING_TURNOVER_CHANGE_WATCH"
    )
    if candidate["watch_type"] != expected_type:
        raise ValueError("M04_RADAR_WATCH_TYPE_CONFLICT")
    price = _positive_decimal(candidate.get("latest_price_idr"))
    previous_price = _positive_decimal(candidate.get("reference_price_idr"))
    volume = _positive_decimal(candidate.get("volume_24h_idr"))
    spread = _positive_decimal(candidate.get("spread_bps"), allow_zero=True)
    # A rolling ticker 24h volume delta is never one-minute traded volume.
    if volume < MIN_TURNOVER_IDR or spread > MAX_SPREAD_BPS:
        raise ValueError("M04_RADAR_PUBLIC_LIQUIDITY_MISMATCH")
    rise = candidate.get("observed_rise_pct")
    if type(rise) is not str or not 1 <= len(rise) <= 64:
        raise ValueError("M04_RADAR_PRICE_CHANGE_INVALID")
    try:
        change = Decimal(rise)
    except InvalidOperation:
        raise ValueError("M04_RADAR_PRICE_CHANGE_INVALID") from None
    if not change.is_finite() or abs(change) > Decimal("500"):
        raise ValueError("M04_RADAR_PRICE_CHANGE_INVALID")
    if abs((price / previous_price - 1) * 100 - change) > Decimal("0.02"):
        raise ValueError("M04_RADAR_PRICE_CHANGE_SOURCE_MISMATCH")

    rolling_delta = candidate.get("volume_24h_net_change_idr")
    rolling_delta_pct = candidate.get("volume_24h_net_change_pct")
    if candidate["volume_watch"]:
        if (_positive_decimal(rolling_delta) <= 0
                or _positive_decimal(rolling_delta_pct) <= 0
                or type(candidate.get("volume_comparison_seconds")) is not int
                or not 60 <= candidate["volume_comparison_seconds"] <= 180
                or type(candidate.get("volume_reference_epoch")) is not int
                or not 0 < candidate["volume_reference_epoch"] < observed):
            raise ValueError("M04_RADAR_ROLLING_DELTA_UNTRUSTED")
    elif rolling_delta is not None or rolling_delta_pct is not None:
        raise ValueError("M04_RADAR_ROLLING_DELTA_CONFLICT")

    # A second, independent check against ORIGINAL source triage. Never
    # accept untrusted candidate-supplied priority as model authority.
    try:
        expected_review = classify_public_watch_pre_ai_nonlive(candidate)
    except (ValueError, TypeError, KeyError, InvalidOperation):
        raise ValueError("M04_RADAR_PRE_AI_UNTRUSTED") from None
    if candidate.get("pre_ai_review") != expected_review:
        raise ValueError("M04_RADAR_PRE_AI_DIGEST_MISMATCH")

    packet = {
        "source_kind": "ORIGINAL_M03_PUBLIC_TICKER_WATCH_UNATTESTED",
        "pair": pair,
        "observed_epoch": observed,
        "reference_epoch": reference,
        "reference_interval_seconds": span,
        "price_snapshot_before_idr": str(previous_price),
        "price_snapshot_after_idr": str(price),
        "observed_rise_pct": str(change),
        "volume_24h_snapshot_idr": str(volume),
        "volume_24h_rolling_net_delta_idr": rolling_delta,
        "volume_24h_rolling_net_delta_pct": rolling_delta_pct,
        "rolling_delta_is_closed_1m_volume": False,
        "spread_snapshot_bps": str(spread),
        "missing_intermediate_minute_samples":
            candidate["missing_intermediate_minute_samples"],
        "watch_type": expected_type,
        "pre_ai_review_priority": expected_review["review_priority"],
        "public_sample_fingerprint_sha256":
            expected_review["public_sample_fingerprint_sha256"],
        "source_is_actual_closed_candle": False,
        "independent_g1_issuer_verified": False,
        "complete_trade_tape_verified": False,
        "maintenance_status": "UNVERIFIED",
        "news_status": "NEWS_UNAVAILABLE",
    }
    return packet


def prepare_original_radar_three_role_handoff_readonly(
    *, snapshot: dict, now_epoch: int, max_candidates: int = 2,
) -> dict[str, Any]:
    """Build one bounded shared evidence packet for each provisional watch.

    Separate immutable digests keep MARKET/RISK/STRATEGY from silently
    receiving different market facts. Those are role INPUT SLOTS, not
    model calls or claims of G1/source/market authorization.
    """
    if (type(now_epoch) is not int or now_epoch <= 0
            or type(max_candidates) is not int or not 1 <= max_candidates <= 2):
        return _blocked("M04_RADAR_HANDOFF_INVALID_SCOPE")
    if (not isinstance(snapshot, dict)
            or snapshot.get("status") != "M03_ORIGINAL_PUBLIC_RADAR_OBSERVED_UNATTESTED"
            or snapshot.get("source_authenticated_for_g1") is not False
            or snapshot.get("three_ai_role_responses_observed") is not False
            or snapshot.get("execution_allowed") is not False
            or not isinstance(snapshot.get("candidates"), list)
            or len(snapshot["candidates"]) > 5):
        return _blocked("M04_RADAR_ORIGINAL_SOURCE_UNVERIFIED")

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    validated: list[tuple[int, int, dict[str, Any], str]] = []
    # Verify the WHOLE original Top5 before prioritizing any AI slot.
    # Public volume-only WATCH with falling price must not displace a
    # higher-quality rising quote just because its rolling-24h delta is
    # larger. Reuse the EXISTING deterministic pre-AI priority, never
    # infer BUY pressure, profitability, G1, or model authority.
    for original_index, candidate in enumerate(snapshot["candidates"]):
        try:
            packet = _source_packet(candidate, now_epoch)
            canonical = json.dumps(
                packet, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            ).encode("ascii")
            review = candidate["pre_ai_review"]
            priority = review["priority_rank_observational"]
            if type(priority) is not int or not 1 <= priority <= 4:
                raise ValueError("M04_RADAR_INVALID_PRE_AI_PRIORITY")
        except (ValueError, KeyError, TypeError, UnicodeError,
                OverflowError, InvalidOperation):
            return _blocked("M04_RADAR_INVALID_CANDIDATE_PACKET")
        pair = packet["pair"]
        if pair in seen:
            return _blocked("M04_RADAR_DUPLICATE_PAIR")
        seen.add(pair)
        validated.append((priority, original_index, packet,
                          sha256(canonical).hexdigest()))
    # Original Top5 ranking is preserved as the deterministic tie-break.
    # This is only an M04 *unexecuted* AI-input priority; M05 radar alerts
    # and M03 scanner order are not touched.
    validated.sort(key=lambda item: (item[0], item[1]))
    for selection_index, (priority, original_index, packet, digest) in enumerate(
            validated[:max_candidates], start=1):
        pair = packet["pair"]
        slots = [{
            "role": role,
            "evidence_digest_sha256": digest,
            "dispatch": _ROLE_DISPATCH,
            "model_output": None,
        } for role in ROLES]
        results.append({
            "pair": pair,
            "evidence_digest_sha256": digest,
            "radar_original_top5_rank": original_index + 1,
            "handoff_selection_rank": selection_index,
            "pre_ai_priority_rank_observational": priority,
            "public_evidence": packet,
            "source_missing": list(_MISSING_FACTS),
            "role_slots": slots,
            "deterministic_pre_ai_market_priority":
                packet["pre_ai_review_priority"],
            "deterministic_pre_ai_risk_status":
                "NO_TRADE_SOURCE_AND_ACCOUNT_UNVERIFIED",
            "deterministic_pre_ai_strategy_status":
                "WAIT_NO_AUTHENTICATED_ENTRY_AND_NET_COSTS",
            "required_net_profit_idr": "10000",
            "required_net_roi_percent": "2",
            "required_threshold_relation": "AND",
            "owner_order_specific_consent_verified": False,
            "execution_allowed": False,
        })
    base = _blocked(
        "M04_RADAR_HANDOFF_PUBLIC_PACKETS_PREPARED"
        if results else "M04_RADAR_HANDOFF_NO_CURRENT_WATCH"
    )
    base["candidate_count"] = len(results)
    base["candidates"] = results
    base["three_role_input_slots_prepared"] = 3 * len(results)
    base["max_candidates_per_pass"] = 2
    base["selection_policy"] = (
        "VALIDATE_ALL_ORIGINAL_TOP5_THEN_PRE_AI_PRIORITY_ASC"
        "_TIE_ORIGINAL_RADAR_RANK"
    )
    base["source_digest_is_g1_receipt"] = False
    return base


def inspect_original_radar_three_role_handoff_readonly(
    *, now_epoch: int | None = None, db_path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    """Call the EXISTING M03 read-only inspector once, never new fetches."""
    now = int(time.time()) if now_epoch is None else now_epoch
    if type(now) is not int or now <= 0:
        return _blocked("M04_RADAR_HANDOFF_INVALID_CLOCK")
    radar = inspect_original_m03_radar_readonly(
        db_path=db_path, now_epoch=now,
    )
    return prepare_original_radar_three_role_handoff_readonly(
        snapshot=radar, now_epoch=now,
    )




def prepare_ws3_three_role_watch_handoff_readonly(
    *, snapshot: dict, now_epoch: int, max_candidates: int = 2,
) -> dict[str, Any]:
    """Source-bound, uncalled MARKET/RISK/STRATEGY input slots for WS3.

    Original handoff, no second scanner, fake spread, paid AI, G1 or orders.
    """
    if (type(now_epoch) is not int or now_epoch <= 0
            or type(max_candidates) is not int or not 1 <= max_candidates <= 2):
        return _blocked("M04_WS3_INVALID_SCOPE")
    prealert_mode = (
        isinstance(snapshot, dict) and snapshot.get("status") ==
        "M03_WS3_MICRO_PREALERT_REVIEW_UNATTESTED"
    )
    if (not isinstance(snapshot, dict)
            or snapshot.get("status") not in (
                "M03_WS3_WATCH_RADAR_OBSERVED_UNATTESTED",
                "M03_WS3_MICRO_PREALERT_REVIEW_UNATTESTED",
            )
            or snapshot.get("source_authenticated_for_g1") is not False
            or snapshot.get("three_ai_role_responses_observed") is not False
            or snapshot.get("execution_allowed") is not False
            or not isinstance(snapshot.get("candidates"), list)
            or len(snapshot["candidates"]) > (40 if prealert_mode else 5)):
        return _blocked("M04_WS3_UNTRUSTED_SOURCE")
    validated = []
    seen = set()
    for row in snapshot["candidates"]:
        try:
            pair, observed, reference = (
                row["pair"], row["observed_epoch"], row["reference_epoch"])
            if (type(pair) is not str or not PAIR_ID.fullmatch(pair)
                    or pair in MAIN_EXCLUDED_PAIRS or pair in seen
                    or type(observed) is not int
                    or not 0 <= now_epoch - observed <= 150
                    or type(reference) is not int or not 0 < reference < observed
                    or row["source"] != "INDODAX_PUBLIC_WS3_SUMMARY_24H"
                    or row["g1_verified"] is not False
                    or row["trade_direction_verified"] is not False
                    or row["bid_ask_spread_verified"] is not False
                    or row["execution_allowed"] is not False
                    or row["momentum_phase"] not in (
                        "EARLY_ACCELERATION_WATCH",
                        "EXTENDED_MOVE_REVIEW",
                        "PRICE_RISE_ROLLING_DELTA_POSITIVE",
                        "PRICE_RISE_ACTIVITY_UNCONFIRMED")
                    or type(row["momentum_priority_rank"]) is not int
                    or not 0 <= row["momentum_priority_rank"] <= 3):
                raise ValueError("M04_WS3_INVALID_PUBLIC_CANDIDATE")
            before = _positive_decimal(row["reference_price_idr"])
            after = _positive_decimal(row["latest_price_idr"])
            turnover = _positive_decimal(row["volume_24h_idr"])
            if turnover < MIN_TURNOVER_IDR:
                raise ValueError("M04_WS3_LOW_TURNOVER")
            rise = _positive_decimal(row["observed_rise_pct"])
            if abs((after / before - 1) * 100 - rise) > Decimal("0.02"):
                raise ValueError("M04_WS3_PRICE_CHANGED")
            delta = Decimal(row["volume_24h_net_change_idr"])
            if not delta.is_finite() or (
                    delta != 0 and delta.adjusted() > 20):
                raise ValueError("M04_WS3_DELTA_INVALID")
            # The original source-bound IDR quality policy is now actually
            # called from M04; previously it was only a standalone module.
            # This ranks PUBLIC evidence, not predicted ROI / BUY confidence.
            quality = candidate_quality_watch(
                candidate=row, now_epoch=now_epoch,
                current_order_budget_idr=None,
            )
            if quality["status"] != "PUBLIC_WATCH_QUALITY_RANKED_NOT_BUY_READY":
                raise ValueError("M04_WS3_QUALITY_INVALID")
            if quality["pair"] != pair or not 0 <= (
                    quality["review_score_out_of_100"]) <= 100:
                raise ValueError("M04_WS3_QUALITY_INCONSISTENT")
            micro = row.get("microstructure_observation")
            if not isinstance(micro, dict):
                raise ValueError("M04_WS3_MICROSTRUCTURE_INVALID")
            if (micro.get("execution_allowed") is not False
                    or micro.get("status") not in (
                        "NOT_OBSERVED", "PARTIAL_SOURCE_OBSERVATION"
                    )):
                raise ValueError("M04_WS3_MICROSTRUCTURE_AUTHORITY_INVALID")
            if micro.get("status") == "NOT_OBSERVED" and (
                    micro.get("trade_activity_observed") is not False
                    or micro.get("orderbook_snapshot_observed") is not False):
                raise ValueError("M04_WS3_MICROSTRUCTURE_CONFLICT")
            if prealert_mode and (
                    row.get("micro_prealert_observation") is not True
                    or micro.get("status") != "PARTIAL_SOURCE_OBSERVATION"
                    or not (micro.get("trade_activity_observed") is True or
                            micro.get("orderbook_snapshot_observed") is True)
                    or rise > Decimal("8") or rise < Decimal("0.05")):
                raise ValueError("M04_WS3_PREALERT_MICRO_EVIDENCE_MISSING")
            if micro.get("status") == "PARTIAL_SOURCE_OBSERVATION" and (
                    micro.get("source_malformed") is True
                    or micro.get("trade_source_gap_or_truncation") is True):
                # Insufficient source continuity: still form a review packet,
                # but place it behind clean source candidates.
                quality = dict(quality, reject_reasons=list(
                    quality["reject_reasons"]
                ) + ["MICRO_SOURCE_GAP_OR_MALFORMED_REVIEW"])
        except (ValueError, KeyError, TypeError, ArithmeticError,
                InvalidOperation, SoloIDRPolicyInvalid):
            return _blocked("M04_WS3_INVALID_CANDIDATE_PACKET")
        seen.add(pair)
        packet = {
            "source_kind": "ORIGINAL_V2_WS3_SUMMARY_WATCH_UNATTESTED",
            "pair": pair, "observed_epoch": observed,
            "reference_epoch": reference,
            "price_before_idr": str(before),
            "price_after_idr": str(after),
            "observed_price_rise_pct": str(rise),
            "rolling_24h_turnover_idr": str(turnover),
            "rolling_24h_net_delta_idr": str(delta),
            "momentum_phase": row["momentum_phase"],
            "quality_review": quality,
            "quality_score_is_trade_probability": False,
            "trade_activity": (
                "PARTIAL_WS3_CAPTURE" if row.get(
                    "microstructure_observation", {},
                ).get("trade_activity_observed") else "NOT_OBSERVED"
            ),
            "orderbook": (
                "PARTIAL_LOCAL_WS3_BOOK_SNAPSHOT" if row.get(
                    "microstructure_observation", {},
                ).get("orderbook_snapshot_observed") else "NOT_OBSERVED"
            ),
            "microstructure_source_evidence": row.get(
                "microstructure_observation", {"status": "NOT_OBSERVED"},
            ),
            "trade_side_aggressor_qualified": False,
            "complete_trade_tape_verified": False,
            "closed_candles": "UNVERIFIED",
            "independent_g1_issuer_verified": False,
            "execution_allowed": False,
        }
        digest = sha256(json.dumps(
            packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        # Score is a non-executable deterministic WATCH ranking, not AI
        # output. Malformed/gapped source must not outrank clean receipts.
        quality_penalty = 100 if "MICRO_SOURCE_GAP_OR_MALFORMED_REVIEW" in (
            quality["reject_reasons"]
        ) else 0
        validated.append((
            quality_penalty, -quality["review_score_out_of_100"],
            row["momentum_priority_rank"], -rise, pair, packet, digest
        ))
    validated.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
    result = _blocked(
        "M04_WS3_HANDOFF_ROLE_INPUTS_PREPARED"
        if validated else "M04_WS3_HANDOFF_NO_CURRENT_WATCH")
    result["source"] = "EXISTING_ORIGINAL_M03_WS3_DB_ONLY"
    result["candidates"] = [{
        "pair": pair, "public_evidence": packet,
        "evidence_digest_sha256": digest,
        "quality_review_observational_only": packet["quality_review"],
        "quality_score_out_of_100": packet["quality_review"]["review_score_out_of_100"],
        "quality_rank_reason": (
            "SOURCE_GAP_OR_MALFORMED_REVIEW_DEPRIORITIZED"
            if source_penalty else
            "QUALITY_EVIDENCE_SCORE_WITH_MOMENTUM_TIEBREAK"
        ),
        "role_slots": [{
            "role": role, "evidence_digest_sha256": digest,
            "dispatch": _ROLE_DISPATCH, "model_output": None,
        } for role in ROLES],
        "missing_evidence": (
            (["partial_real_trade_activity_source_not_observed"]
             if packet["trade_activity"] == "NOT_OBSERVED" else [])
            + (["fresh_orderbook_snapshot_not_observed"]
               if packet["orderbook"] == "NOT_OBSERVED" else [])
            + [
                "complete_trade_tape_and_aggressor_flow_unverified",
                "stable_orderbook_depth_and_slippage_unverified",
                "closed_1m_5m_15m_candles",
                "complete_15m_1h_tape_and_independent_g1",
                "verified_capital_fees_and_account_ownership",
            ]
        ),
        "execution_allowed": False,
    } for source_penalty, _, _, _, pair, packet, digest in (
        validated[:max_candidates]
    )]
    result["candidate_count"] = len(result["candidates"])
    result["three_role_input_slots_prepared"] = len(result["candidates"]) * 3
    result["actual_three_role_ai_calls"] = 0
    result["quality_scored_candidates_before_top2"] = len(validated)
    result["selection_policy"] = (
        "SOURCE_CLEAN_FIRST_QUALITY_SCORE_DESC_MOMENTUM_PHASE_TIEBREAK_"
        "NOT_PREDICTED_ROI_OR_FINAL_AI"
    )
    result["real_trade_book_data_can_be_partial"] = True
    return result


def inspect_original_ws3_three_role_handoff_readonly(
    *, now_epoch: int | None = None, db_path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    """Existing WS3 inspector to existing M04 handoff, no network or writes."""
    from .m05_radar_momentum_nonlive import (
        inspect_original_m03_ws3_watch_readonly,
    )
    now = int(time.time()) if now_epoch is None else now_epoch
    if type(now) is not int or now <= 0:
        return _blocked("M04_WS3_INVALID_CLOCK")
    snap = inspect_original_m03_ws3_watch_readonly(
        db_path=db_path, now_epoch=now,
    )
    # Preserve the precise upstream READ-ONLY file/query failure. No-source
    # or permission failure is not an AI market REJECT and must not get
    # hidden under the generic "untrusted watch candidate" validation.
    upstream_statuses = {
        "M05_WS3_SOURCE_UNTRUSTED": "M04_WS3_SOURCE_FILE_UNTRUSTED",
        "M05_WS3_SOURCE_ABSENT": "M04_WS3_SOURCE_TABLE_ABSENT",
        "M05_WS3_SOURCE_QUERY_UNAVAILABLE": "M04_WS3_SOURCE_QUERY_UNAVAILABLE",
    }
    source_status = snap.get("status") if isinstance(snap, dict) else None
    if source_status in upstream_statuses:
        result = _blocked(upstream_statuses[source_status])
        result["upstream_source_status"] = source_status
        return result
    return prepare_ws3_three_role_watch_handoff_readonly(
        snapshot=snap, now_epoch=now,
    )

def main() -> int:
    print(json.dumps(
        inspect_original_radar_three_role_handoff_readonly(),
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ))
    return 0  # An offline evidence inspection is not AI/trading acceptance.


if __name__ == "__main__":
    raise SystemExit(main())
