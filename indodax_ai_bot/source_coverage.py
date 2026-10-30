"""M04-D: qualify public market evidence before any real G1 receipt.

This classifier describes what the current Indodax PUBLIC adapter actually
observed. Recent trades have no proven exhaustive pagination/window coverage.
No amount of public OHLC continuity or indicator scoring upgrades that claim.
No issuer keys, network access, or executable signals live here.
"""
from __future__ import annotations

from typing import Any, Mapping
from decimal import Decimal


def _closed_candle_ohlc_check(obs: Any, interval: int) -> str:
    """Compare closed native/aggregate OHLC using only observed 1m rows.

    A match is a PUBLIC arithmetic consistency check, not completeness or
    source provenance. No gaps are filled with synthetic candles.
    """
    target = obs.candle_5m if interval == 300 else obs.candle_15m
    bars = getattr(obs, "candles_1m", ())
    if (type(target.start) is not int or target.interval != interval
            or target.start <= 0 or target.start % interval != 0):
        return "REJECT_TARGET_CANDLE_INTERVAL"
    if not isinstance(bars, (tuple, list)):
        return "UNKNOWN_NO_CONTIGUOUS_1M"
    expected = tuple(range(target.start, target.start + interval, 60))
    observed = [x for x in bars if type(x.start) is int
                and target.start <= x.start < target.start + interval]
    if (len(observed) != len(expected)
            or any(x.interval != 60 for x in observed)
            or tuple(sorted(x.start for x in observed)) != expected):
        return "UNKNOWN_NO_CONTIGUOUS_1M"
    ordered = sorted(observed, key=lambda x: x.start)
    consistent = (
        target.open == ordered[0].open
        and target.high == max(x.high for x in ordered)
        and target.low == min(x.low for x in ordered)
        and target.close == ordered[-1].close
    )
    return ("MATCHED_CLOSED_OHLC_UNATTESTED" if consistent
            else "REJECT_CLOSED_OHLC_MISMATCH")


def _public_timeframe_diagnostic(obs: Any, screening_epoch: int) -> dict[str, Any]:
    """Original M04-D admission: explicit observed vs UNKNOWN evidence.

    1m/5m/15m OHLC, quote, depth, server_time and ticker timestamps come
    from several PUBLIC REST calls, never a single atomic book/tape snapshot.
    No independent clock sync, upstream authentication, trade completeness,
    source issuer or paid model entitlement can be inferred from this.
    """
    receipt = obs.snapshot.source
    one, five, fifteen = obs.candle_1m, obs.candle_5m, obs.candle_15m
    end, observed = receipt.closed_1m_epoch, receipt.observed_epoch
    age = observed - end
    screening_lag = observed - screening_epoch
    aligned = (
        type(screening_epoch) is int
        and type(end) is int and end > 0
        and type(observed) is int
        and type(one.start) is int and one.interval == 60
        and type(five.start) is int and five.interval == 300
        and type(fifteen.start) is int and fifteen.interval == 900
        and one.start % 60 == 0 and one.start + 60 == end
        and five.start % 300 == 0 and fifteen.start % 900 == 0
        and 0 <= age <= 120 and 0 <= screening_lag <= 30
        and 0 <= end - (five.start + 300) <= 300
        and 0 <= end - (fifteen.start + 900) <= 900
        and receipt.pair == obs.pair
    )
    exchange_epoch = getattr(obs, "exchange_server_epoch", None)
    skew = (observed - exchange_epoch
            if type(exchange_epoch) is int and exchange_epoch > 0 else None)
    clock_status = ("UNKNOWN_NO_EXCHANGE_EPOCH" if skew is None
                    else "PUBLIC_SERVER_CLOCK_WITHIN_30S_UNATTESTED"
                    if abs(skew) <= 30 else "REJECT_PUBLIC_SERVER_CLOCK_SKEW")
    ticker_epoch = getattr(obs, "ticker_observed_epoch", None)
    ticker_age = (observed - ticker_epoch
                  if type(ticker_epoch) is int and ticker_epoch > 0 else None)
    ticker_status = ("UNKNOWN_NO_TICKER_EPOCH" if ticker_age is None
                     else "PUBLIC_TICKER_FRESH_UNATTESTED"
                     if -3 <= ticker_age <= 60 else "REJECT_PUBLIC_TICKER_AGE")

    bid_book = getattr(obs, "bid_book_levels", ())
    ask_book = getattr(obs, "ask_book_levels", ())
    book_status = "UNKNOWN_NO_BOTH_BOOK_LADDERS"
    if isinstance(bid_book, tuple) and isinstance(ask_book, tuple) and bid_book and ask_book:
        def valid_levels(levels: tuple, *, bid_side: bool) -> bool:
            if len(levels) > 10:
                return False
            for row in levels:
                if not isinstance(row, tuple) or len(row) != 2:
                    return False
                p, q = row
                if (not isinstance(p, Decimal) or not isinstance(q, Decimal)
                        or not p.is_finite() or not q.is_finite()
                        or p <= 0 or q <= 0):
                    return False
            prices = [row[0] for row in levels]
            return all((a > b if bid_side else a < b)
                       for a, b in zip(prices, prices[1:]))
        if (valid_levels(bid_book, bid_side=True)
                and valid_levels(ask_book, bid_side=False)
                and bid_book[0][0] <= ask_book[0][0]):
            book_status = "PUBLIC_TWO_SIDED_DEPTH_OBSERVED_NONATOMIC"
        else:
            book_status = "REJECT_BOOK_LADDER_CONSISTENCY"

    ohlc5 = _closed_candle_ohlc_check(obs, 300)
    ohlc15 = _closed_candle_ohlc_check(obs, 900)
    if clock_status.startswith("REJECT_") or ticker_status.startswith("REJECT_"):
        aligned = False
    if book_status.startswith("REJECT_") or ohlc5.startswith("REJECT_") or ohlc15.startswith("REJECT_"):
        aligned = False

    return {
        "status": ("ALIGNED_PUBLIC_TIMESTAMPS_UNATTESTED"
                   if aligned else "REJECT_PUBLIC_TIMEFRAME_OR_AGE"),
        "closed_1m_start_epoch": one.start,
        "closed_1m_end_epoch": end,
        "closed_5m_start_epoch": five.start,
        "closed_5m_end_epoch": five.start + five.interval,
        "closed_15m_start_epoch": fifteen.start,
        "closed_15m_end_epoch": fifteen.start + fifteen.interval,
        "snapshot_observed_epoch": observed,
        "screening_epoch": screening_epoch,
        "snapshot_age_after_1m_close_seconds": age,
        "screen_to_observation_seconds": screening_lag,
        "exchange_to_local_clock_skew_seconds": skew,
        "exchange_clock_status": clock_status,
        "ticker_age_seconds": ticker_age,
        "ticker_freshness_status": ticker_status,
        "closed_5m_ohlc_consistency": ohlc5,
        "closed_15m_ohlc_consistency": ohlc15,
        "book_ladder_consistency": book_status,
        "book_and_quote_atomic_snapshot_proven": False,
        "best_bid_ask_visible": True,
        "orderbook_depth_levels_visible": obs.depth_levels_visible,
        "closed_1m_volume_observed": str(one.volume),
        "recent_trade_rows_visible": obs.trade_count_visible,
        "independent_source_origin_verified": False,
        "full_trade_tape_proven": False,
        "g1_issuer_connected": False,
    }


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
                "timeframe_diagnostic": None,
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
        # Preserve existing primary source-trust reason for downstream
        # contracts. Per-field operational faults belong in diagnostic.
        diagnostic = _public_timeframe_diagnostic(obs, screening_epoch)
        details.append({
            "pair": pair, "observation_present": True,
            "recent_trade_rows_visible": obs.trade_count_visible,
            "closed_1m_trades_visible": obs.visible_closed_1m_trade_count,
            "closed_1m_epoch": receipt.closed_1m_epoch,
            "source_label": receipt.provenance,
            "can_prove_complete_trade_tape": False,
            "timeframe_diagnostic": diagnostic,
            "reason": reason,
        })
    observed = sum(item["observation_present"] for item in details)
    timing_aligned = sum(
        item["timeframe_diagnostic"] is not None
        and item["timeframe_diagnostic"]["status"] == "ALIGNED_PUBLIC_TIMESTAMPS_UNATTESTED"
        for item in details
    )
    return {
        "stage": "M04D_PUBLIC_SOURCE_COVERAGE",
        "status": "REJECT_SOURCE_NOT_QUALIFIED",
        "reason": "PUBLIC_TRADE_TAPE_COMPLETENESS_AND_ORIGIN_NOT_PROVEN",
        "pairs_requested": len(selected_pairs),
        "pairs_observed": observed,
        "pairs_with_aligned_public_timestamps": timing_aligned,
        "pairs_with_missing_or_invalid_public_timestamps": len(selected_pairs) - timing_aligned,
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
