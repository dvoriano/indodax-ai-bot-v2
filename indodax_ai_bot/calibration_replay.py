"""M03 observational OHLC-only ablation. NEVER a real-trade backtest.

Uses locally archived, immutable-as-observed PUBLIC closed 1m candles.
No orderbook/trade tape/exchange-signed provenance, fills, actual fees,
wallet balance or three-role market/risk/strategy evidence is available.
This is an explicitly limited, predeclared proxy study; it MUST NOT turn
on a strategy, G1 issuer, paid provider, or trading.
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from statistics import mean

from .public_market import Candle, PublicCandleHistory, PublicDataError


_ZERO = Decimal("0")
_HUNDRED = Decimal("10000")
# Predeclared illustrative ROUNDTRIP cost cases: not Indodax fee schedule.
_SCENARIO_COST_BPS = (Decimal("0"), Decimal("20"), Decimal("50"))
# 14 days of closed 1m candles permits at most ~100 independent 30% holdout
# hourly windows, unlike the former three-day cap (~21 windows) which made
# the default 30-sample holdout requirement mathematically impossible.
MAX_CALIBRATION_1M_ROWS = 14 * 24 * 60


def _bps(first: Decimal, second: Decimal) -> Decimal:
    if first <= 0:
        raise PublicDataError("INVALID_CALIBRATION_PRICE")
    return (second / first - 1) * _HUNDRED


def _mean_str(rows: list[Decimal]) -> str | None:
    return str(round(mean(rows), 4)) if rows else None


def evaluate_public_ohlc_proxy_ablation(
    history: PublicCandleHistory, *,
    min_holdout_independent_signals: int = 30,
    owner_assumed_roundtrip_friction_bps: Decimal | None = None,
    hypothetical_ticket_idr: Decimal | None = None,
) -> dict:
    """Chronological holdout, purged 60m horizon, nonoverlapping 60m samples.

    An observed signal at the CLOSE of minute i can only enter hypothetically
    at the OPEN of i+1. Exit proxies are actual future recorded close prices,
    not executions. NO optimized thresholds, walk-forward tuning or fitted
    strategy parameters. Train/holdout statistical estimates are descriptive.
    """
    if (not isinstance(history, PublicCandleHistory)
            or history.provenance != "INDODAX_PUBLIC_OHLC_V2_UNATTESTED"
            or history.source_trust_verified is not False
            or history.trade_history_complete is not False
            or type(min_holdout_independent_signals) is not int
            or not 10 <= min_holdout_independent_signals <= 1000):
        raise PublicDataError("INVALID_CALIBRATION_SOURCE")
    # Cost and IDR ticket are caller-supplied assumptions ONLY. They cannot
    # establish actual Indodax tier fee, spread, slippage, fills or profit.
    if ((owner_assumed_roundtrip_friction_bps is None) !=
            (hypothetical_ticket_idr is None)):
        raise PublicDataError("M03_CALIBRATION_FRICTION_AND_TICKET_REQUIRED_TOGETHER")
    if owner_assumed_roundtrip_friction_bps is not None:
        if (type(owner_assumed_roundtrip_friction_bps) is not Decimal
                or not owner_assumed_roundtrip_friction_bps.is_finite()
                or not Decimal("0") <= owner_assumed_roundtrip_friction_bps <= Decimal("3000")
                or type(hypothetical_ticket_idr) is not Decimal
                or not hypothetical_ticket_idr.is_finite()
                or not Decimal("1") <= hypothetical_ticket_idr <= Decimal("1000000000000")):
            raise PublicDataError("M03_CALIBRATION_OWNER_ASSUMPTION_INVALID")
    bars = history.candles
    if (len(bars) < 240 or len(bars) > MAX_CALIBRATION_1M_ROWS
            or history.to_epoch - history.from_epoch != 60 * len(bars)):
        raise PublicDataError("INSUFFICIENT_CALIBRATION_HISTORY")
    for idx, bar in enumerate(bars):
        if (not isinstance(bar, Candle)
                or bar.start != history.from_epoch + idx * 60
                or bar.interval != 60
                or bar.volume < 0 or bar.open <= 0 or bar.close <= 0
                or bar.low <= 0 or bar.low > min(bar.open, bar.close)
                or bar.high < max(bar.open, bar.close)
                or not all(x.is_finite() for x in (
                    bar.open, bar.high, bar.low, bar.close, bar.volume))):
            raise PublicDataError("INVALID_CALIBRATION_CANDLE")
    if bars[-1].start + 60 != history.to_epoch:
        raise PublicDataError("INVALID_CALIBRATION_WINDOW")

    payload = [
        [v.start, str(v.open), str(v.high), str(v.low),
         str(v.close), str(v.volume)] for v in bars
    ]
    local_digest = sha256(json.dumps(
        payload, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
    # i >=30 has 15/10/5m preceding observations. i+60 must be closed.
    indices = list(range(30, len(bars) - 60, 60))
    if len(indices) < 3:
        raise PublicDataError("INSUFFICIENT_CALIBRATION_WINDOWS")
    first_holdout_index = max(1, int(len(indices) * 0.7))
    first_holdout_bar = indices[first_holdout_index]
    train = []
    holdout = []
    purged = 0
    for k, i in enumerate(indices):
        if k < first_holdout_index and i + 60 >= first_holdout_bar:
            purged += 1
            continue
        close = bars[i].close
        ret5 = _bps(bars[i-5].close, close)
        ret15 = _bps(bars[i-15].close, close)
        prev10high = max(x.high for x in bars[i-10:i])
        vol_avg = sum((x.volume for x in bars[i-10:i]), _ZERO) / Decimal(10)
        volume_ratio = (bars[i].volume / vol_avg
                        if vol_avg > 0 else _ZERO)
        break_high = close > prev10high
        proxy_surge = ret5 >= Decimal("80") and break_high
        proxy_pullback = (ret15 >= Decimal("80")
                          and close > bars[i-1].close
                          and _bps(close, prev10high) >= Decimal("25"))
        exhausted = ret5 >= Decimal("150") and volume_ratio >= Decimal("4")
        # Inputs used are only <=i; no future response may alter the labels.
        flags = {
            "ALL_HOURLY_CONTROL": True,
            "SURGE_NO_VOLUME_ABLATION": proxy_surge and not exhausted,
            "SURGE_WITH_VOLUME_PROXY": proxy_surge and volume_ratio >= 2 and not exhausted,
            "PULLBACK_NO_VOLUME_ABLATION": proxy_pullback and not exhausted,
            "PULLBACK_WITH_VOLUME_PROXY": proxy_pullback and volume_ratio >= 1 and not exhausted,
            "EXHAUSTION_CAUTION_CONTROL": exhausted,
        }
        entry = bars[i+1].open  # hypothetical, NOT observed fill
        record = {
            "signal_close_epoch": bars[i].start + 60,
            "flags": flags,
            "forward_30m_bps": _bps(entry, bars[i+30].close),
            "forward_60m_bps": _bps(entry, bars[i+60].close),
        }
        (train if k < first_holdout_index else holdout).append(record)

    def summarize(rows: list[dict]) -> dict:
        cohorts = {}
        for name in (
            "ALL_HOURLY_CONTROL",
            "SURGE_NO_VOLUME_ABLATION", "SURGE_WITH_VOLUME_PROXY",
            "PULLBACK_NO_VOLUME_ABLATION", "PULLBACK_WITH_VOLUME_PROXY",
            "EXHAUSTION_CAUTION_CONTROL",
        ):
            selected = [r for r in rows if r["flags"][name]]
            gross30 = [r["forward_30m_bps"] for r in selected]
            gross60 = [r["forward_60m_bps"] for r in selected]
            # Each historical signal uses exactly one hypothetical
            # buy-ticket basis. BOTH thresholds are required, not OR.
            # This is a descriptive counterfactual, not executable edge.
            friction = owner_assumed_roundtrip_friction_bps
            ticket = hypothetical_ticket_idr
            n30 = ([x - friction for x in gross30]
                   if friction is not None else None)
            n60 = ([x - friction for x in gross60]
                   if friction is not None else None)
            def joint_count(net_samples: list[Decimal] | None) -> int | None:
                if net_samples is None or ticket is None:
                    return None
                return sum(
                    net_bps >= Decimal("200")
                    and ticket * net_bps / _HUNDRED >= Decimal("10000")
                    for net_bps in net_samples
                )
            cohorts[name] = {
                "independent_hourly_samples": len(selected),
                "owner_assumed_net_30m_bps_average": (
                    _mean_str(n30) if n30 is not None else None),
                "owner_assumed_net_60m_bps_average": (
                    _mean_str(n60) if n60 is not None else None),
                "owner_assumed_joint_idr10000_and_roi2pct_30m_samples": joint_count(n30),
                "owner_assumed_joint_idr10000_and_roi2pct_60m_samples": joint_count(n60),
                "avg_gross_30m_bps": _mean_str(gross30),
                "avg_gross_60m_bps": _mean_str(gross60),
                "illustrative_net_30m_bps_by_roundtrip_cost": {
                    str(int(cost)): _mean_str([x-cost for x in gross30])
                    for cost in _SCENARIO_COST_BPS
                },
                "fill_or_realized_profit_verified": False,
            }
        return {"independent_hourly_windows": len(rows), "cohorts": cohorts}

    study = summarize(train), summarize(holdout)
    holdout_with_volume = [
        study[1]["cohorts"][name]["independent_hourly_samples"] for name in (
            "SURGE_WITH_VOLUME_PROXY", "PULLBACK_WITH_VOLUME_PROXY",
        )
    ]
    return {
        "stage": "M03_PUBLIC_OHLC_PROXY_ABLATION_READONLY",
        "status": "DESCRIPTIVE_OOS_ONLY_INSUFFICIENT_TRADE_EVIDENCE",
        "pair": history.pair,
        "source": "IMMUTABLE_AS_OBSERVED_PUBLIC_OHLC_NOT_EXCHANGE_SIGNED",
        "source_digest_sha256_local": local_digest,
        "closed_1m_rows_visible": len(bars),
        "maximum_supported_closed_1m_rows": MAX_CALIBRATION_1M_ROWS,
        "owner_assumed_roundtrip_friction_bps": (
            str(owner_assumed_roundtrip_friction_bps)
            if owner_assumed_roundtrip_friction_bps is not None else None),
        "hypothetical_ticket_idr": (
            str(hypothetical_ticket_idr)
            if hypothetical_ticket_idr is not None else None),
        "owner_assumed_friction_is_actual_exchange_fee_verified": False,
        "owner_assumed_joint_threshold_is_qualification": False,
        "signal_interval_minutes": 60,
        "forward_horizons_minutes": [30, 60],
        "holdout_fraction_planned": "0.30",
        "purged_boundary_hourly_windows": purged,
        "train": study[0],
        "holdout": study[1],
        "holdout_proxy_signal_count_minimum_met": (
            all(x >= min_holdout_independent_signals for x in holdout_with_volume)
        ),
        "separate_nonindependent_trade_tape_study_required": True,
        "actual_indodax_fee_verified": False,
        "actual_fill_verified": False,
        "net_idr_10000_and_roi_2pct_verified": False,
        "out_of_sample_strategy_edge_validated": False,
        "market_source_g1_verified": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
        "final_decision": "REJECT",
    }
