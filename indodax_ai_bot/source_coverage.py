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
