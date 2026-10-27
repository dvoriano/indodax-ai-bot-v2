"""M04-D: qualify public market evidence before any real G1 receipt.

This classifier describes what the current Indodax PUBLIC adapter actually
observed. Recent trades have no proven exhaustive pagination/window coverage.
No amount of public OHLC continuity or indicator scoring upgrades that claim.
No issuer keys, network access, or executable signals live here.
"""
from __future__ import annotations

from typing import Any, Mapping


def public_coverage_report(
    selected_pairs: tuple[str, ...],
    observations: Mapping[str, Any],
    *,
    screening_epoch: int,
    complete_batch: bool,
    window_expired: bool,
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    for pair in selected_pairs:
        obs = observations.get(pair)
        if obs is None:
            details.append({
                "pair": pair, "observation_present": False,
                "recent_trade_rows_visible": 0, "closed_1m_trades_visible": 0,
                "closed_1m_epoch": None, "source_label": None,
                "can_prove_complete_trade_tape": False,
                "reason": "NO_PUBLIC_OBSERVATION",
            })
            continue
        receipt = obs.snapshot.source
        reason = "PUBLIC_RECENT_TRADE_WINDOW_NOT_EXHAUSTIVELY_PROVEN"
        if obs.trade_history_complete or receipt.verified:
            reason = "UNVERIFIABLE_PUBLIC_COMPLETENESS_OR_TRUST_CLAIM"
        elif (receipt.observed_epoch < receipt.closed_1m_epoch
              or receipt.observed_epoch - screening_epoch > 30
              or receipt.observed_epoch < screening_epoch):
            reason = "PUBLIC_SOURCE_CLOCK_MISMATCH"
        details.append({
            "pair": pair, "observation_present": True,
            "recent_trade_rows_visible": obs.trade_count_visible,
            "closed_1m_trades_visible": obs.visible_closed_1m_trade_count,
            "closed_1m_epoch": receipt.closed_1m_epoch,
            "source_label": receipt.provenance,
            "can_prove_complete_trade_tape": False,
            "reason": reason,
        })
    observed = sum(item["observation_present"] for item in details)
    return {
        "stage": "M04D_PUBLIC_SOURCE_COVERAGE",
        "status": "REJECT_SOURCE_NOT_QUALIFIED",
        "reason": "PUBLIC_TRADE_TAPE_COMPLETENESS_AND_ORIGIN_NOT_PROVEN",
        "pairs_requested": len(selected_pairs),
        "pairs_observed": observed,
        "batch_complete": complete_batch,
        "batch_window_expired": window_expired,
        "public_trades_are_recent_sample": True,
        "public_trade_history_complete": False,
        "independently_authenticated_market_origin": False,
        "snapshot_time_consistency_proven": False,
        "real_g1_issuer_connected": False,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
        "pair_coverage": details,
    }


def assess_public_ws_btc_for_g1(
    ws_sample: Mapping[str, Any] | None,
    comparison: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """M04-R: source eligibility for the EXISTING bulk G1 admission path.

    A locally continuous WS segment is NOT complete market history. A
    rebaseline marker cannot date every missing upstream trade. This is
    diagnostic evidence only and never upgrades an unsigned public source
    into an independently qualified issuer.
    """
    ws = ws_sample if isinstance(ws_sample, Mapping) else {}
    joined = comparison if isinstance(comparison, Mapping) else {}
    gaps = ws.get("unrecoverable_gap_rebaseline_count")
    gap_count = gaps if type(gaps) is int and 0 <= gaps <= 10_000 else None
    minute = ws.get("closed_1m_epoch")
    closed_minute = minute if type(minute) is int and minute > 0 and minute % 60 == 0 else None
    rejected_gap = ws.get("reason") in (
        "REJECT_WS_MINUTE_CROSSES_RECORDED_GAP",
        "REJECT_WS_REBASELINE_IN_CLOSED_MINUTE",
        "REJECT_WS_GAP_LEDGER_INVALID",
        "REJECT_WS_GAP_LEDGER_LIMIT_EXCEEDED",
        "REJECT_WS_GAP_LEDGER_INCONSISTENT",
    )
    comparable = joined.get("status") in (
        "PUBLIC_VISIBLE_SAMPLES_MATCH_UNATTESTED",
        "PUBLIC_VISIBLE_SAMPLES_DIFFER_UNATTESTED",
    )
    if rejected_gap:
        reason = "RECORDED_GAP_IN_BTC_CLOSED_MINUTE"
    elif ws.get("status") not in ("PUBLIC_SAMPLE_OBSERVED", "PUBLIC_SAMPLE_EMPTY"):
        reason = "NO_VALID_BTC_WS_CLOSED_MINUTE"
    elif gap_count is None:
        reason = "BTC_WS_GAP_HISTORY_NOT_VALIDATED"
    elif gap_count:
        reason = "HISTORIC_WS_GAP_AND_TRADE_TAPE_UNATTESTED"
    else:
        reason = "PUBLIC_WS_TRADE_TAPE_AND_ORIGIN_UNATTESTED"
    return {
        "stage": "M04R_EXISTING_G1_BTC_WS_SOURCE_QUALITY",
        "status": "REJECT_SOURCE_NOT_QUALIFIED",
        "reason": reason,
        "closed_1m_start_epoch": closed_minute,
        "rest_ws_comparable_public_minute": comparable,
        "ws_observation_status": ws.get("status") if isinstance(ws.get("status"), str) else None,
        "ws_minute_source_reason": ws.get("reason") if isinstance(ws.get("reason"), str) else None,
        "known_unrecoverable_ws_discontinuities": gap_count,
        "current_minute_recorded_gap": bool(rejected_gap),
        "observed_ws_segment_cannot_attest_full_tape": True,
        "rest_recent_trades_not_exhaustive": True,
        "independent_exchange_origin_verified": False,
        "full_closed_minute_trade_coverage_proven": False,
        "real_g1_issuer_connected": False,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
    }
