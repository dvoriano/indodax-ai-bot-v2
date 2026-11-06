"""Evidence-only scalping situation report for an existing M03 DeepCandidate.

No HTTP, AI, exchange orders, inference of complete trade tape or new scanner.
Continued strong trends remain WATCH-eligible even if already extended:
observing strength is different from clearing a high-RSI/overheat risk gate.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Mapping


def _number(raw: object, *, signed: bool = False) -> Decimal | None:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
        return None
    try:
        val = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if not val.is_finite() or abs(val) > Decimal("1e25"):
        return None
    if not signed and val < 0:
        return None
    return val


def _count(raw: object) -> int | None:
    return raw if type(raw) is int and 0 <= raw <= 1000000 else None


def project_scalping_evidence_nonlive(summary: Mapping[str, object]) -> dict:
    """Additive original-M03 evidence, never a new entry recommendation.

    A surge does not expire because of the 24h gain percentage. WATCH
    continuation remains visible, including an overheated breakout case,
    while existing anomaly/risk gates continue to block executable BUY.
    """
    if not isinstance(summary, Mapping):
        raise ValueError("SCALPING_EVIDENCE_REQUIRES_SUMMARY")
    stage_detail = summary.get("scalp_setup_stage")
    if not isinstance(stage_detail, Mapping):
        stage_detail = {}
    stage = stage_detail.get("stage")
    structure = summary.get("market_structure_diagnostic")
    structure_label = (
        structure.get("label") if isinstance(structure, Mapping) else None
    )
    cautions = summary.get("anomaly_flags")
    anomaly = isinstance(cautions, (list, tuple)) and len(cautions) > 0
    if stage == "BREAKOUT_CONTINUATION_WATCH" and not anomaly:
        watch = "MOMENTUM_CONTINUATION_WATCH"
    elif (stage == "EXHAUSTED_PUMP_CAUTION" and
          structure_label == "SURGE_BREAKOUT_OBSERVED"):
        watch = "MOMENTUM_CONTINUATION_CAUTION_WATCH"
    elif stage == "EARLY_ACCELERATION_WATCH":
        watch = "EARLY_MOMENTUM_WATCH"
    elif stage == "PULLBACK_REBOUND_WATCH":
        watch = "PULLBACK_REBOUND_WATCH"
    elif stage in ("EXHAUSTED_PUMP_CAUTION", "FALSE_BREAKOUT_CAUTION"):
        watch = "REVERSAL_OR_EXHAUSTION_CAUTION"
    else:
        watch = "NO_CONFIRMED_SCALPING_SETUP"

    buy_count = _count(summary.get("visible_buy_trade_count_sampled"))
    sell_count = _count(summary.get("visible_sell_trade_count_sampled"))
    buy_value = _number(summary.get("visible_buy_idr"))
    sell_value = _number(summary.get("visible_sell_idr"))
    ratio = _number(summary.get("volume_ratio"))
    spread = _number(summary.get("spread_bps"))
    imbalance = _number(summary.get("book_imbalance"), signed=True)
    tape = {
        "visible_buy_count_sampled": buy_count,
        "visible_sell_count_sampled": sell_count,
        "visible_buy_value_idr_sampled": (
            str(buy_value) if buy_value is not None else None
        ),
        "visible_sell_value_idr_sampled": (
            str(sell_value) if sell_value is not None else None
        ),
        "sample_buy_minus_sell_count": (
            buy_count - sell_count
            if buy_count is not None and sell_count is not None else None
        ),
        "sample_buy_minus_sell_value_idr": (
            str(buy_value - sell_value)
            if buy_value is not None and sell_value is not None else None
        ),
        "coverage": "PARTIAL_PUBLIC_TRADES_ONLY",
        "complete_trade_tape_verified": False,
        "verified_aggressor_buy_dominance": False,
    }
    return {
        "schema": "ORIGINAL_M03_SCALPING_EVIDENCE_V1",
        "watch_classification": watch,
        "already_pumped_not_automatic_exclusion": True,
        "watch_not_order_recommendation": True,
        "observed_closed_1m_epoch": (
            summary.get("closed_1m_epoch")
            if type(summary.get("closed_1m_epoch")) is int else None
        ),
        "structure_label": structure_label,
        "ticker_minute_momentum": (
            "OBSERVED_UNATTESTED"
            if isinstance(summary.get("early_momentum"), Mapping)
            else "NOT_OBSERVED"
        ),
        "ticker_observations_not_closed_1m_candles": True,
        "relative_closed_candle_volume": (
            str(ratio) if ratio is not None else None
        ),
        "relative_volume_coverage": (
            "OBSERVED_UNATTESTED" if ratio is not None else "UNKNOWN"
        ),
        "rolling_24h_change_not_actual_1m_volume": True,
        "trade_flow": tape,
        "order_book": {
            "spread_bps": str(spread) if spread is not None else None,
            "imbalance_snapshot": (
                str(imbalance) if imbalance is not None else None
            ),
            "wall_snapshot_available": isinstance(
                summary.get("book_wall_diagnostic"), Mapping
            ),
            "persistence_or_execution_verified": False,
        },
        "news_and_announcements": "UNAVAILABLE_IN_ORIGINAL_CANDIDATE",
        "news_source_and_published_at_verified": False,
        "exchange_maintenance_status_verified": False,
        "market_context_and_btc_issuer_verified_here": False,
        "anomaly_review_required": bool(anomaly) or (
            stage == "EXHAUSTED_PUMP_CAUTION"
        ),
        "three_ai_roles_evaluated_here": False,
        "profit_after_fees_and_slippage_proven": False,
        "execution_allowed": False,
    }
