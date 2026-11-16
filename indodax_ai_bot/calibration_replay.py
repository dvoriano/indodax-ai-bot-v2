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


def _public_proxy_gate_flags(bars: tuple[Candle, ...], i: int) -> tuple[dict, dict]:
    """Single predeclared predicate shared by OOS and read-only diagnostics."""
    close = bars[i].close
    ret5 = _bps(bars[i-5].close, close)
    ret15 = _bps(bars[i-15].close, close)
    prev10high = max(x.high for x in bars[i-10:i])
    vol_avg = sum((x.volume for x in bars[i-10:i]), _ZERO) / Decimal(10)
    volume_ratio = (bars[i].volume / vol_avg if vol_avg > 0 else _ZERO)
    break_high = close > prev10high
    proxy_surge = ret5 >= Decimal("80") and break_high
    proxy_pullback = (ret15 >= Decimal("80")
                      and close > bars[i-1].close
                      and _bps(close, prev10high) >= Decimal("25"))
    exhausted = ret5 >= Decimal("150") and volume_ratio >= Decimal("4")
    flags = {
        "ALL_HOURLY_CONTROL": True,
        "SURGE_NO_VOLUME_ABLATION": proxy_surge and not exhausted,
        "SURGE_WITH_VOLUME_PROXY": proxy_surge and volume_ratio >= 2 and not exhausted,
        "PULLBACK_NO_VOLUME_ABLATION": proxy_pullback and not exhausted,
        "PULLBACK_WITH_VOLUME_PROXY": proxy_pullback and volume_ratio >= 1 and not exhausted,
        "EXHAUSTION_CAUTION_CONTROL": exhausted,
    }
    gates = {
        "surge_5m_ge_80bps": ret5 >= Decimal("80"),
        "surge_break_above_prior_10m_high": break_high,
        "surge_price_setup": proxy_surge,
        "surge_volume_ge_2x": volume_ratio >= 2,
        "surge_price_and_volume": proxy_surge and volume_ratio >= 2,
        "pullback_15m_ge_80bps": ret15 >= Decimal("80"),
        "pullback_close_above_previous_minute": close > bars[i-1].close,
        "pullback_prior_10m_high_ge_25bps_above_close":
            _bps(close, prev10high) >= Decimal("25"),
        "pullback_price_setup": proxy_pullback,
        "pullback_volume_ge_1x": volume_ratio >= 1,
        "pullback_price_and_volume": proxy_pullback and volume_ratio >= 1,
        "not_exhausted": not exhausted,
        "surge_final": flags["SURGE_WITH_VOLUME_PROXY"],
        "pullback_final": flags["PULLBACK_WITH_VOLUME_PROXY"],
    }
    return flags, gates


def evaluate_public_ohlc_proxy_ablation(
    history: PublicCandleHistory, *,
    min_holdout_independent_signals: int = 30,
    owner_assumed_roundtrip_friction_bps: Decimal | None = None,
    hypothetical_ticket_idr: Decimal | None = None,
    diagnose_signal_gates: bool = False,
) -> dict:
    """Chronological holdout, purged 60m horizon, nonoverlapping 60m samples.

    An observed signal at the CLOSE of minute i can only enter hypothetically
    at the OPEN of i+1. Exit proxies are actual future recorded close prices,
    not executions. NO optimized thresholds, walk-forward tuning or fitted
    strategy parameters. Train/holdout statistical estimates are descriptive.
    """
    if type(diagnose_signal_gates) is not bool:
        raise PublicDataError("INVALID_CALIBRATION_DIAGNOSTIC_MODE")
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
        flags, _ = _public_proxy_gate_flags(bars, i)
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
    diagnostic = None
    if diagnose_signal_gates:
        # These exploratory per-minute frequencies are NOT independent
        # OOS trades, exchange fills or threshold optimization.
        def gate_funnel(sample_indices) -> dict:
            counts = None
            surge_bins: set[int] = set()
            pullback_bins: set[int] = set()
            inspected = 0
            for j in sample_indices:
                _, gates = _public_proxy_gate_flags(bars, j)
                if counts is None:
                    counts = {k: 0 for k in gates}
                inspected += 1
                for key, yes in gates.items():
                    counts[key] += int(yes)
                if gates["surge_final"]:
                    surge_bins.add(j // 60)
                if gates["pullback_final"]:
                    pullback_bins.add(j // 60)
            return {
                "candidate_minutes_examined": inspected,
                "gate_true_counts": counts or {},
                "distinct_60m_archive_bins_with_surge": len(surge_bins),
                "distinct_60m_archive_bins_with_pullback": len(pullback_bins),
            }
        diagnostic = {
            "status": "EXPLORATORY_READONLY_GATE_FREQUENCY_NOT_OOS_EDGE",
            "same_original_predeclared_thresholds": True,
            "anchor_minute_offset_in_archive": 30,
            "anchor_stride_minutes": 60,
            "holdout_hourly_fixed_anchor": gate_funnel(
                indices[first_holdout_index:]),
            "holdout_all_eligible_minutes_not_independent_trades": gate_funnel(
                range(first_holdout_bar, len(bars) - 60)),
            "threshold_tuning_performed": False,
            "market_source_g1_verified": False,
        }
    return {
        "stage": "M03_PUBLIC_OHLC_PROXY_ABLATION_READONLY",
        **({"signal_gate_diagnostic": diagnostic} if diagnostic is not None else {}),
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

def evaluate_m04_review_history_once(journal, *, now_epoch: int, evidence_db=None,
                                     max_reviews: int = 100) -> dict:
    """Persist mature 5/15/30/60m PUBLIC ticker-mark outcomes, not fills.

    Anchor to the first eligible observation at/after decision, never its
    pre-decision signal price. Endpoint is first eligible observation at/
    after each target; both exchange and local timestamps must be in the
    60s sampling window. Missing/expired coverage remains UNKNOWN forever
    for this completed observation, not a fabricated flat return. No
    network, AI calls, automatic learning or strategy changes.
    """
    import sqlite3
    from pathlib import Path
    from contextlib import closing
    from .journal import DecisionJournal
    from .m03_ws3_evidence_archive_nonlive import (
        DEFAULT_EVIDENCE_DB, _private_owned_regular,
    )
    if (not isinstance(journal, DecisionJournal)
            or getattr(journal, "_m11_readonly_fixture", False)
            or type(now_epoch) is not int or now_epoch <= 0
            or type(max_reviews) is not int or not 1 <= max_reviews <= 100):
        raise ValueError("M04_HISTORY_SCOPE_INVALID")
    path = Path(DEFAULT_EVIDENCE_DB if evidence_db is None else evidence_db).expanduser()
    base = {"stage":"M04_HISTORICAL_EVALUATION_NONLIVE",
            "status":"M04_HISTORY_SOURCE_UNAVAILABLE",
            "reviews_evaluated":0,"outcomes_recorded":0,
            "execution_allowed":False,"actual_provider_verified":False,
            "realized_profit_verified":False,"strategy_auto_updated":False,
            "horizons_minutes":[5,15,30,60],
            "proxy_roundtrip_cost_bps":["20","50"]}
    if (path.resolve()==journal._db_path.resolve()
            or not _private_owned_regular(path)):
        return base
    horizons = (5,15,30,60)
    def mark(con,pair,start):
        rows=con.execute("""SELECT e.source_exchange_epoch,c.recorded_local_epoch,
                       e.last_idr,e.public_offset
                   FROM ws3_source_evidence e JOIN ws3_source_cycles c
                   ON e.minute_epoch=c.minute_epoch
                   WHERE e.pair=? AND e.minute_epoch BETWEEN ? AND ?
                   AND e.source_status='ELIGIBLE_SOURCE_OBSERVATION'
                   AND e.source_exchange_epoch BETWEEN ? AND ?
                   AND c.recorded_local_epoch BETWEEN ? AND ?
                   ORDER BY e.source_exchange_epoch,c.recorded_local_epoch
                   LIMIT 8""",
                   (pair,start-start%60,start+60,start,start+60,start,min(now_epoch,start+60))).fetchall()
        for event,recorded,price,offset in rows:
            try:
                n=Decimal(price)
                if (type(event) is int and type(recorded) is int
                        and start<=event<=recorded<=min(now_epoch,start+60)
                        and type(offset) is int and offset>=0 and n.is_finite()
                        and n>0 and -20<=n.adjusted()<=20):
                    return {"exchange_epoch":event,"recorded_epoch":recorded,
                            "last_idr":str(n),"public_offset":offset}
            except (TypeError,ValueError,ArithmeticError):
                continue
        return None
    pending=[]
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,timeout=5)) as src:
            src.execute("PRAGMA query_only=ON")
            src.execute("BEGIN")
            # Validate schema before creating any outcomes.
            src.execute("SELECT source_exchange_epoch,last_idr,public_offset,source_status "
                        "FROM ws3_source_evidence LIMIT 0")
            src.execute("SELECT recorded_local_epoch FROM ws3_source_cycles LIMIT 0")
            with journal._connect() as con:
                # Serialize anchor and outcomes so simultaneous evaluators
                # cannot persist differing anchors for the same review.
                con.execute("BEGIN IMMEDIATE")
                journal._m04_history_tables(con)
                reviews=con.execute("""SELECT r.pair,r.digest,r.decision_epoch
                    FROM m04_history_reviews r WHERE r.decision_epoch<=?
                    AND (SELECT COUNT(*) FROM m04_history_outcomes o
                        WHERE o.pair=r.pair AND o.digest=r.digest)
                        < CASE WHEN r.decision_epoch<=? THEN 4
                               WHEN r.decision_epoch<=? THEN 3
                               WHEN r.decision_epoch<=? THEN 2 ELSE 1 END
                    ORDER BY r.decision_epoch,r.pair LIMIT ?""",
                    (now_epoch-360,now_epoch-3660,now_epoch-1860,
                     now_epoch-960,max_reviews)).fetchall()
                for pair,digest,decision in reviews:
                    existing_rows=con.execute(
                        "SELECT horizon_minutes,outcome_json FROM m04_history_outcomes "
                        "WHERE pair=? AND digest=? ORDER BY horizon_minutes",
                        (pair,digest)).fetchall()
                    existing={x[0] for x in existing_rows}
                    # Freeze the first evaluated anchor across horizons,
                    # including UNKNOWN, even after archive retention expires.
                    anchor=(json.loads(existing_rows[0][1])["anchor"]
                            if existing_rows else mark(src,pair,decision))
                    for h in horizons:
                        target=decision+h*60
                        if h in existing or now_epoch<target+60:
                            continue
                        end=mark(src,pair,target)
                        item={"status":"UNKNOWN_MISSING_MARK","anchor":anchor,"endpoint":end,
                              "target_epoch":target,"evaluation_epoch":now_epoch,
                              "gross_move_bps":None,"net_proxy_bps":None,
                              "actual_fill_or_profit":False}
                        if anchor is not None and end is not None:
                            gross=_bps(Decimal(anchor["last_idr"]),Decimal(end["last_idr"]))
                            item.update(status="PUBLIC_MARK_MOVE_OBSERVED",
                                        gross_move_bps=str(gross),
                                        net_proxy_bps={str(cost):str(gross-cost)
                                            for cost in (Decimal("20"),Decimal("50"))})
                        pending.append((pair,digest,h,json.dumps(item,sort_keys=True)))
                recorded=0
                for row in pending:
                    recorded+=con.execute("INSERT OR IGNORE INTO m04_history_outcomes VALUES(?,?,?,?)",row).rowcount
    except (sqlite3.Error,OSError,ValueError,ArithmeticError):
        return base
    with journal._connect() as con:
        # Group by decision disposition and horizon; UNKNOWN has its own
        # denominator. A favorable mark on a rejected pair is a review lead,
        # not proof that the veto was wrong or a trade would have filled.
        cohorts={}
        for review_json,h,outcome_json in con.execute(
                "SELECT r.review_json,o.horizon_minutes,o.outcome_json "
                "FROM m04_history_outcomes o JOIN m04_history_reviews r "
                "ON r.pair=o.pair AND r.digest=o.digest"):
            review=json.loads(review_json)
            outcome=json.loads(outcome_json)
            label=("SELECTED_SYNTHETIC" if review["selected_synthetic_review"]
                   else review["disposition"])
            row=cohorts.setdefault(label+":"+str(h),{"observed":0,"unknown":0,
                                                    "net_proxy_50bps_ge_2pct":0})
            if outcome["status"]!="PUBLIC_MARK_MOVE_OBSERVED":
                row["unknown"]+=1
            else:
                row["observed"]+=1
                row["net_proxy_50bps_ge_2pct"]+=int(
                    Decimal(outcome["net_proxy_bps"]["50"])>=200)
    return dict(base,status="M04_HISTORY_EVALUATED",reviews_evaluated=len(reviews),
                outcomes_recorded=recorded,cohorts=cohorts)
