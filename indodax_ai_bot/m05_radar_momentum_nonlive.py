"""M03→M05 owner radar WATCH from ORIGINAL V2 minute ticker evidence only.

Optional one-send-per-existing-M05-loop informational notice. Reads the SAME
original M03 DecisionJournal SQLite in query_only mode; uses the SAME M05
owner SQLite to durably claim BEFORE Telegram send. No new scanner, HTTP
price fetch, second bot, order, M04 provider, owner PASS/REJECT proposal,
or G1 independent-source promotion.

A gap between two actual ticker samples is called out, never interpolated
as a 1m candle or certified 1m return. The user must separately opt into
M05_RADAR_MOMENTUM_ALERT_GO=YES on the original M05 service after the ongoing
24h M05 heartbeat audit. Importing this module never changes the service.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path
import os
import re
import sqlite3
import stat
import time
from zoneinfo import ZoneInfo

from .scanner import MAIN_EXCLUDED_PAIRS

DEFAULT_DB = Path.home() / "indodax-ai-bot-v2" / "runtime" / "decisions.sqlite3"
MIN_TURNOVER_IDR = Decimal("150000000")
MIN_RISE_PCT = Decimal("8")
MIN_EARLY_RISE_PCT = Decimal("5")
# This is a positive NET difference in two rolling-24h ticker snapshots,
# NOT actual one-minute trades, buy/sell pressure or complete trade flow.
MIN_TURNOVER_NET_CHANGE_IDR = Decimal("20000000")
MIN_TURNOVER_NET_CHANGE_PCT = Decimal("3")
TURNOVER_MAX_WINDOW_SECONDS = 180
MAX_SPREAD_BPS = Decimal("100")
MAX_AGE_SECONDS = 90
LOOKBACK_SECONDS = 300
PAIR_COOLDOWN_SECONDS = 900
GLOBAL_SPACING_SECONDS = 60
PREMIUM_GLOBAL_SPACING_SECONDS = 15
PAIR_ID = re.compile(r"^[a-z0-9]+_idr$")


def _owned_private_regular(path: Path) -> bool:
    try:
        st = path.lstat()
        return (stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
                and st.st_nlink == 1 and st.st_mode & 0o077 == 0
                and st.st_size > 0)
    except OSError:
        return False


def _finite_number(value, *, positive=False) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if (not n.is_finite() or abs(n.adjusted()) > 20
            or (n <= 0 if positive else n < 0)):
        return None
    return n



def classify_public_watch_pre_ai_nonlive(candidate: dict) -> dict:
    """One-snapshot public-only evidence triage, NOT three actual AI calls.

    Informational sorting/review metadata from the candidate's SAME cached
    original M03 rows; no network, orderbook invention, new scanner, AI,
    trading permissions or extra Telegram messages.
    """
    price = Decimal(candidate["observed_rise_pct"])
    spread = Decimal(candidate["spread_bps"])
    combined = candidate["price_watch"] and candidate["volume_watch"]
    if price <= 0:
        priority = "ACTIVITY_ONLY_PRICE_NOT_RISING"
        rank = 4
    elif combined:
        priority = "PRICE_AND_ROLLING_DELTA_REVIEW"
        rank = 1
    elif candidate["price_watch"]:
        priority = "PRICE_MOMENTUM_REVIEW"
        rank = 2
    else:
        priority = "ROLLING_DELTA_PRICE_WEAK_REVIEW"
        rank = 3
    cautions = ["PUBLIC_SOURCE_UNATTESTED", "TRADE_TAPE_NOT_COMPLETE",
                "ROLLING_24H_DELTA_NOT_EXECUTED_1M_VOLUME",
                "ORDERBOOK_DEPTH_NEWS_AND_MAINTENANCE_UNVERIFIED"]
    if candidate["missing_intermediate_minute_samples"]:
        cautions.append("MISSING_INTERMEDIATE_MINUTE_SAMPLE")
    if price <= 0:
        cautions.append("PRICE_NONPOSITIVE_DO_NOT_INFER_BUY_PRESSURE")
    if spread > Decimal("60"):
        cautions.append("SPREAD_OVER_60_BPS_OBSERVED")
    # Diagnostic fingerprint only. Local hash never authenticates Indodax,
    # creates a G1 issuer receipt or authorizes paid M04 models.
    fields = {
        "pair": candidate["pair"],
        "observed_epoch": candidate["observed_epoch"],
        "reference_epoch": candidate["reference_epoch"],
        "observed_rise_pct": candidate["observed_rise_pct"],
        "volume_24h_idr": candidate["volume_24h_idr"],
        "volume_24h_net_change_idr": candidate["volume_24h_net_change_idr"],
        "spread_bps": candidate["spread_bps"],
        "sample_gap": candidate["missing_intermediate_minute_samples"],
        "watch_type": candidate["watch_type"],
    }
    digest = sha256(json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")).hexdigest()
    return {
        "status": "PUBLIC_WATCH_PRE_AI_DIAGNOSTIC_ONLY",
        "review_priority": priority,
        "priority_rank_observational": rank,
        "public_sample_fingerprint_sha256": digest,
        "caution_codes": cautions,
        "price_is_actual_closed_candle": False,
        "delta_24h_is_actual_1m_trade_volume": False,
        "trade_direction_buy_dominance_verified": False,
        "g1_verified": False,
        "market_risk_strategy_ai_calls": 0,
        "ai_role_results": None,
        "news_status": "UNAVAILABLE",
        "strategy": "WATCH_NO_TRADE",
        "execution_allowed": False,
    }



def inspect_original_m03_radar_readonly(
    *, db_path: Path = DEFAULT_DB, now_epoch: int | None = None,
    explain_pairs: tuple[str, ...] = (),
    replay_no_casebook: bool = False,
) -> dict:
    """Bounded factual top-candidate radar; not an order/entry signal.

    The latest *actual* minute sample must be <=90s old. Referenced 2–5m
    price is the oldest retained sample within a 5-minute period. Missing
    intermediate minutes are disclosed, not filled in. A casebook EARLY_WATCH
    is provenance of an uncalibrated historical watch, never G1 or AI.
    """
    now = int(time.time()) if now_epoch is None else now_epoch
    if type(now) is not int or now <= 0:
        raise ValueError("M03_RADAR_INVALID_CLOCK")
    if (type(explain_pairs) is not tuple or len(explain_pairs) > 10
            or len(set(explain_pairs)) != len(explain_pairs)
            or any(type(pair) is not str or not PAIR_ID.fullmatch(pair)
                   for pair in explain_pairs)
            or type(replay_no_casebook) is not bool):
        raise ValueError("M03_RADAR_EXPLAIN_INVALID_ARGS")
    requested = set(explain_pairs)
    diagnostics = {}
    path = Path(db_path)
    if not _owned_private_regular(path):
        return {"status": "M03_RADAR_ORIGINAL_DB_NOT_TRUSTED", "candidates": []}
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            source_tables = {
                x[0] for x in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('public_ticker_minute_samples','public_surge_cases')"
                ).fetchall()
            }
            if "public_ticker_minute_samples" not in source_tables:
                return {"status": "M03_RADAR_ORIGINAL_SAMPLE_TABLE_ABSENT",
                        "candidates": []}
            # Bounded indexed same ORIGINAL M03 archive, no network I/O.
            rows = db.execute(
                "SELECT pair,minute_epoch,observed_epoch,last_idr,"
                "volume_24h_idr,spread_bps "
                "FROM public_ticker_minute_samples "
                "WHERE minute_epoch BETWEEN ? AND ? "
                "ORDER BY minute_epoch DESC LIMIT 4000",
                (now - LOOKBACK_SECONDS - 60, now),
            ).fetchall()
            watches = set()
            if "public_surge_cases" in source_tables and not replay_no_casebook:
                for pair, minute, cohort in db.execute(
                    "SELECT pair,minute_epoch,cohort FROM public_surge_cases "
                    "WHERE minute_epoch BETWEEN ? AND ? LIMIT 1000",
                    (now - LOOKBACK_SECONDS, now),
                ).fetchall():
                    if (type(pair) is str and PAIR_ID.fullmatch(pair)
                            and type(minute) is int
                            and cohort == "EARLY_WATCH"):
                        watches.add((pair, minute))
    except (sqlite3.Error, OSError, ValueError):
        return {"status": "M03_RADAR_ORIGINAL_QUERY_UNAVAILABLE",
                "candidates": []}

    by_pair = defaultdict(list)
    for pair, minute, observed, price, volume, spread in rows:
        if (type(pair) is not str or not PAIR_ID.fullmatch(pair)
                or pair in MAIN_EXCLUDED_PAIRS
                or type(minute) is not int or minute <= 0 or minute % 60 != 0
                or type(observed) is not int
                or observed < minute or observed >= minute + 60
                or observed > now):
            continue
        p = _finite_number(price, positive=True)
        v = _finite_number(volume, positive=True)
        s = _finite_number(spread)
        if p is None or v is None or s is None:
            continue
        by_pair[pair].append((minute, observed, p, v, s))

    candidates = []
    for pair, samples in by_pair.items():
        latest = samples[0]  # ORDER BY minute_epoch DESC
        if now - latest[1] > MAX_AGE_SECONDS:
            if pair in requested:
                diagnostics[pair] = {"reason": "STALE_LATEST_SAMPLE",
                                     "latest_observed_epoch": latest[1]}
            continue
        if latest[3] < MIN_TURNOVER_IDR:
            if pair in requested:
                diagnostics[pair] = {"reason": "BELOW_DAILY_TURNOVER_FLOOR",
                                     "latest_observed_epoch": latest[1]}
            continue
        if latest[4] > MAX_SPREAD_BPS:
            if pair in requested:
                diagnostics[pair] = {"reason": "SPREAD_OVER_WATCH_LIMIT",
                                     "latest_observed_epoch": latest[1]}
            continue
        older = [
            x for x in samples[1:]
            if 60 <= latest[0] - x[0] <= LOOKBACK_SECONDS
        ]
        if not older:
            if pair in requested:
                diagnostics[pair] = {"reason": "MISSING_2_TO_5M_REFERENCE",
                                     "latest_observed_epoch": latest[1]}
            continue
        reference = min(older, key=lambda x: x[0])
        rise_pct = (latest[2] / reference[2] - 1) * Decimal(100)
        if not rise_pct.is_finite() or rise_pct > Decimal("500"):
            if pair in requested:
                diagnostics[pair] = {"reason": "NONFINITE_OR_OUTLIER_PRICE_RISE",
                                     "latest_observed_epoch": latest[1]}
            continue
        early = any(
            p == pair and latest[0] - 120 <= minute <= latest[0]
            for p, minute in watches
        )
        price_watch = (
            rise_pct >= MIN_RISE_PCT or
            (early and rise_pct >= MIN_EARLY_RISE_PCT)
        )
        # Require exact adjacent minute samples for a snapshot-delta watch.
        # Rolling 24h turnover has an entering and an expiring edge: the
        # POSITIVE net change is descriptive, NOT measured one-minute volume.
        volume_prior = []
        for offset in range(1, TURNOVER_MAX_WINDOW_SECONDS // 60 + 1):
            older_at_minute = next(
                (x for x in samples[1:]
                 if x[0] == latest[0] - offset * 60), None
            )
            if older_at_minute is None:
                break
            volume_prior.append(older_at_minute)
        volume_base = volume_prior[-1] if volume_prior else None
        volume_delta = (
            latest[3] - volume_base[3]
            if volume_base is not None else None
        )
        volume_pct = (
            volume_delta / volume_base[3] * Decimal(100)
            if volume_delta is not None and volume_base[3] > 0 else None
        )
        volume_watch = (
            volume_delta is not None and volume_pct is not None
            and volume_delta >= MIN_TURNOVER_NET_CHANGE_IDR
            and volume_pct >= MIN_TURNOVER_NET_CHANGE_PCT
        )
        if not price_watch and not volume_watch:
            if pair in requested:
                diagnostics[pair] = {
                    "reason": "BELOW_PRICE_AND_ROLLING_DELTA_THRESHOLDS",
                    "observed_rise_pct": str(rise_pct.quantize(Decimal("0.01"))),
                    "rolling_24h_delta_observable": volume_delta is not None,
                    "latest_observed_epoch": latest[1],
                }
            continue
        actual_minutes = {x[0] for x in samples
                          if reference[0] <= x[0] <= latest[0]}
        expected = (latest[0] - reference[0]) // 60 + 1
        watch_type = (
            "PRICE_AND_ROLLING_TURNOVER_WATCH"
            if price_watch and volume_watch else
            "PRICE_MOMENTUM_WATCH" if price_watch else
            "ROLLING_TURNOVER_CHANGE_WATCH"
        )
        candidate = {
            "pair": pair,
            "watch_type": watch_type,
            "price_watch": price_watch,
            "volume_watch": volume_watch,
            "volume_24h_net_change_idr": (
                format(volume_delta, "f") if volume_watch else None
            ),
            "volume_24h_net_change_pct": (
                format(volume_pct.quantize(Decimal("0.01")), "f")
                if volume_watch else None
            ),
            "volume_reference_epoch": (
                volume_base[1] if volume_watch else None
            ),
            "volume_comparison_seconds": (
                latest[0] - volume_base[0] if volume_watch else None
            ),
            "volume_24h_previous_idr": (
                str(volume_base[3]) if volume_watch else None
            ),
            "rolling_24h_turnover_not_1m_traded_volume": True,
            "observed_epoch": latest[1],
            "reference_epoch": reference[1],
            "latest_price_idr": str(latest[2]),
            "reference_price_idr": str(reference[2]),
            "observed_rise_pct": format(rise_pct.quantize(Decimal("0.01")), "f"),
            "reference_interval_seconds": latest[0] - reference[0],
            "missing_intermediate_minute_samples": len(actual_minutes) < expected,
            "casebook_early_watch_observed": early,
            "sample_age_seconds": now - latest[1],
            "volume_24h_idr": str(latest[3]),
            "spread_bps": str(latest[4]),
            "no_g1_or_ai_or_order_authority": True,
        }
        candidate["pre_ai_review"] = classify_public_watch_pre_ai_nonlive(candidate)
        candidates.append(candidate)
        if pair in requested:
            diagnostics[pair] = {"reason": "QUALIFIED_BEFORE_TOP5",
                                 "latest_observed_epoch": latest[1],
                                 "watch_type": watch_type}
    # Both price and volume change outrank either alone. This is NOT an
    # executable ranking, backtested opportunity score or predicted ROI.
    candidates.sort(key=lambda x: (
        0 if x["price_watch"] and x["volume_watch"] else
        1 if x["price_watch"] else 2,
        -Decimal(x["observed_rise_pct"]) if x["price_watch"]
        else -Decimal(x["volume_24h_net_change_idr"]),
        x["pair"],
    ))
    if requested:
        for index, candidate in enumerate(candidates):
            pair = candidate["pair"]
            if pair in requested:
                diagnostics[pair]["reason"] = (
                    "IN_TOP5" if index < 5 else "QUALIFIED_OUTSIDE_TOP5"
                )
                diagnostics[pair]["rank"] = index + 1
        for pair in requested:
            diagnostics.setdefault(pair, {
                "reason": "NO_VALID_SAMPLE_IN_REPLAY_WINDOW",
            })
    result = {
        "status": "M03_ORIGINAL_PUBLIC_RADAR_OBSERVED_UNATTESTED",
        "candidates": candidates[:5],
        "ranked_review_shortlist_top40": candidates[:40],
        "source_authenticated_for_g1": False,
        "three_ai_role_responses_observed": False,
        "owner_order_specific_final_verified": False,
        "execution_allowed": False,
    }
    if requested:
        result["pair_diagnostics"] = {
            pair: diagnostics[pair] for pair in explain_pairs
        }
    return result



# An explicit source-isolated WS3 radar. It does NOT adapt WS3 data into
# public_ticker_minute_samples: WS3 has no verified spread or 1m trade volume.
WS3_MAX_AGE_SECONDS = 150
WS3_MIN_PRICE_RISE_PCT = Decimal("0.5")

WS3_EARLY_MOVE_MIN_PCT = Decimal("0.35")
WS3_EXTENDED_MOVE_PCT = Decimal("8")


def _ws3_price_activity_review(samples: list, latest: tuple,
                               previous: tuple) -> dict:
    """Review only real minute-bucket WS3 quote samples.

    Rolling 24h turnover snapshots are not 1m executed volume or BUY/SELL
    activity. An early ramp requires three adjacent observed minutes and
    two positive, accelerating price steps with positive 24h turnover delta.
    """
    delta = latest[3] - previous[3]
    step = (latest[2] / previous[2] - 1) * 100
    older = next((
        x for x in samples[2:]
        if x[0] == previous[0] - 60 and x[1] < previous[1]
    ), None) if latest[0] - previous[0] == 60 else None
    prior = (previous[2] / older[2] - 1) * 100 if older else None
    total = (latest[2] / older[2] - 1) * 100 if older else None
    accelerating = (prior is not None and total is not None
                    and prior > 0 and step > prior
                    and total >= WS3_EARLY_MOVE_MIN_PCT and delta > 0)
    if step >= WS3_EXTENDED_MOVE_PCT or (
            total is not None and total >= WS3_EXTENDED_MOVE_PCT):
        phase, priority = "EXTENDED_MOVE_REVIEW", 3
    elif accelerating:
        phase, priority = "EARLY_ACCELERATION_WATCH", 0
    elif delta > 0:
        phase, priority = "PRICE_RISE_ROLLING_DELTA_POSITIVE", 1
    else:
        phase, priority = "PRICE_RISE_ACTIVITY_UNCONFIRMED", 2
    return {
        "momentum_phase": phase,
        "momentum_priority_rank": priority,
        "compared_source_minute_samples": 3 if older else 2,
        "prior_price_step_pct": format(prior, ".4f") if prior is not None else None,
        "latest_price_step_pct": format(step, ".4f"),
        "two_step_price_change_pct": format(total, ".4f")
        if total is not None else None,
        "rolling_24h_delta_positive": delta > 0,
        "actual_1m_executed_volume_idr": None,
        "trade_buy_sell_counts": None,
        "trade_buy_sell_idr": None,
        "orderbook_spread_bps": None,
        "orderbook_depth_imbalance": None,
        "three_ai_role_responses": None,
        "missing_microstructure": [
            "TRADE_ACTIVITY_NOT_OBSERVED",
            "ORDERBOOK_NOT_OBSERVED",
            "CLOSED_1M_CANDLE_UNVERIFIED",
            "G1_INDEPENDENT_ATTESTATION_UNVERIFIED",
            "THREE_AI_RESPONSES_NOT_CALLED",
        ],
    }



def _ws3_micro_observation(row: tuple, *, now: int,
                            source_minute: int) -> dict:
    """Validate same original M03 micro archive before using it for WATCH.

    No public sample can act as G1 issuer/order authority, even when source
    trade activity and book data are both observed.
    """
    unknown = {"status": "NOT_OBSERVED", "trade_activity_observed": False,
               "orderbook_snapshot_observed": False,
               "source_trade_side_not_aggressor_verified": True,
               "complete_trade_tape_verified": False,
               "execution_allowed": False}
    if not row:
        return unknown
    (pair, minute, captured, frames, buys, sells, buy_idr, sell_idr,
     last_trade, trade_offset, trade_gap, truncated, book_offset, book_epoch,
     best_bid, best_ask, spread, bid_depth, ask_depth,
     imbalance, malformed) = row
    if (type(minute) is not int or type(captured) is not int
            or not 0 <= now - captured <= 150
            or abs(minute - source_minute) > 60
            or type(frames) is not int or not 0 <= frames <= 600
            or malformed not in (0, 1)
            or trade_gap not in (0, 1) or truncated not in (0, 1)):
        return unknown
    out = dict(unknown)
    out["status"] = "PARTIAL_SOURCE_OBSERVATION"
    out["source_capture_epoch"] = captured
    out["source_malformed"] = bool(malformed)
    out["trade_source_gap_or_truncation"] = bool(trade_gap or truncated)
    if frames > 0 and (type(buys) is int and type(sells) is int
                       and 0 <= buys + sells <= 1000
                       and type(last_trade) is int
                       and 0 <= now - last_trade <= 150):
        buy = _finite_number(buy_idr)
        sell = _finite_number(sell_idr)
        if buy is not None and sell is not None:
            out.update({
                "trade_activity_observed": True,
                "source_reported_buy_count": buys,
                "source_reported_sell_count": sells,
                "source_reported_buy_idr": format(buy, "f"),
                "source_reported_sell_idr": format(sell, "f"),
                "captured_trade_count": buys + sells,
                "capture_trade_offset": trade_offset,
                "last_trade_exchange_epoch": last_trade,
            })
    if (type(book_epoch) is int
            and 0 <= now - book_epoch <= 150
            and type(book_offset) is int and book_offset >= 0):
        bid = _finite_number(best_bid, positive=True)
        ask = _finite_number(best_ask, positive=True)
        bid_idr = _finite_number(bid_depth)
        ask_idr = _finite_number(ask_depth)
        pub_spread = _finite_number(spread)
        if (bid is not None and ask is not None and bid < ask
                and bid_idr is not None and ask_idr is not None
                and pub_spread is not None):
            expected_spread = (ask - bid) / ((ask + bid) / 2) * 10000
            if abs(expected_spread - pub_spread) < Decimal("0.02"):
                out.update({
                    "orderbook_snapshot_observed": True,
                    "best_bid_idr": str(bid),
                    "best_ask_idr": str(ask),
                    "spread_bps": format(pub_spread, ".4f"),
                    "bid_depth_top5_idr": format(bid_idr, "f"),
                    "ask_depth_top5_idr": format(ask_idr, "f"),
                    "orderbook_source_offset": book_offset,
                    "orderbook_locally_observed_epoch": book_epoch,
                })
    if not out["trade_activity_observed"] and not out[
            "orderbook_snapshot_observed"]:
        return unknown
    return out


def _ws3_observation_priority(row: dict) -> int:
    """Tie-break source-backed microstructure, NOT an executable AI score."""
    micro = row["microstructure_observation"]
    if not micro.get("orderbook_snapshot_observed"):
        return 2
    spread = Decimal(micro["spread_bps"])
    if spread > Decimal("150"):
        return 4
    if micro.get("trade_activity_observed"):
        if (micro["captured_trade_count"] > 0
                and spread <= Decimal("70")
                and Decimal(micro["source_reported_buy_idr"])
                > Decimal(micro["source_reported_sell_idr"])):
            return 0  # Source-reported sides ONLY, not buyer aggression.
        return 1
    return 3


def inspect_original_m03_ws3_micro_discovery_readonly(
    *, db_path: Path = DEFAULT_DB, now_epoch: int | None = None,
    max_pairs: int = 2,
) -> dict:
    """Select up to two source-fresh IDR pairs for real WS3 micro CAPTURE.

    Deliberately independent of M05 Telegram alert thresholds. Acquiring
    real trades/book is the PRECONDITION for evidence-quality assessment.
    Reads only ORIGINAL M03 SQLite; no new scanner, transport or data writes.
    When price acceleration is absent, rotate a bounded set of genuinely
    liquid recently published pairs to gather truthful market evidence.
    """
    now = int(time.time()) if now_epoch is None else now_epoch
    if (type(now) is not int or now <= 0
            or type(max_pairs) is not int or not 1 <= max_pairs <= 2):
        raise ValueError("M03_WS3_MICRO_DISCOVERY_ARGUMENT_INVALID")
    path = Path(db_path).expanduser().resolve()
    empty = {
        "status": "M03_MICRO_DISCOVERY_NO_FRESH_LIQUID_SOURCE",
        "pairs": [], "fresh_liquid_pair_count": 0,
        "weak_price_rise_pair_count": 0,
        "selection_is_m05_alert": False,
        "trade_activity_observed": False,
        "orderbook_observed": False,
        "execution_allowed": False,
    }
    if not _owned_private_regular(path):
        return dict(empty, status="M03_MICRO_DISCOVERY_DB_UNTRUSTED")
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='m03_ws_summary_watch_minute'"
            ).fetchone()
            if not exists:
                return dict(empty, status="M03_MICRO_DISCOVERY_SUMMARY_ABSENT")
            rows = db.execute(
                "SELECT pair,minute_epoch,exchange_epoch,last_idr,"
                "rolling_24h_idr,public_offset "
                "FROM m03_ws_summary_watch_minute "
                "WHERE minute_epoch BETWEEN ? AND ? "
                "ORDER BY minute_epoch DESC LIMIT 2000",
                (now - 360, now),
            ).fetchall()
    except (sqlite3.Error, OSError, ValueError):
        return dict(empty, status="M03_MICRO_DISCOVERY_QUERY_UNAVAILABLE")
    by_pair = defaultdict(list)
    for pair, minute, exchange, raw_price, raw_turnover, offset in rows:
        if (type(pair) is not str or not PAIR_ID.fullmatch(pair)
                or pair in MAIN_EXCLUDED_PAIRS
                or type(minute) is not int or minute % 60 != 0
                or type(exchange) is not int
                or type(offset) is not int or offset < 0
                or not -90 <= exchange - minute < 60
                or exchange > now):
            continue
        price = _finite_number(raw_price, positive=True)
        turnover = _finite_number(raw_turnover, positive=True)
        if price is None or turnover is None:
            continue
        if len(by_pair[pair]) < 5:
            by_pair[pair].append((minute, exchange, price, turnover, offset))
    rising, fallback = [], []
    for pair, samples in by_pair.items():
        latest = samples[0]
        if (not 0 <= now - latest[0] <= WS3_MAX_AGE_SECONDS
                or not 0 <= now - latest[1] <= WS3_MAX_AGE_SECONDS
                or latest[3] < MIN_TURNOVER_IDR):
            continue
        previous = next((
            old for old in samples[1:]
            if 60 <= latest[0] - old[0] <= 300
            and old[1] < latest[1]
        ), None)
        rise = (
            (latest[2] / previous[2] - 1) * 100
            if previous is not None else None
        )
        delta_positive = (
            latest[3] > previous[3] if previous is not None else False
        )
        if rise is not None and 0 < rise <= Decimal("8"):
            tier = (
                0 if rise <= Decimal("3") and delta_positive else
                1 if delta_positive else 2
            )
            rising.append((tier, -rise, -latest[3], pair))
        else:
            fallback.append((-latest[3], pair))
    rising.sort()
    fallback.sort()
    # Preserve the top source-backed rising pair, but let the SECOND capture
    # slot sample an actual below-Telegram-threshold early mover. Otherwise
    # two already-visible +1–3% pairs can starve a +0.1–0.4% liquid pair
    # of real trade/book observations until its move becomes obvious.
    # This is source COVERAGE rotation, never a BUY ranking or alert.
    ranked_risers = [item[3] for item in rising]
    chosen = ranked_risers[:1] if ranked_risers else []
    early_scout_pool = [
        item[3] for item in rising
        if item[0] == 0
        and -item[1] < WS3_MIN_PRICE_RISE_PCT
        and item[3] not in chosen
    ][:20]
    if max_pairs > 1 and early_scout_pool:
        scout = early_scout_pool[(now // 60) % len(early_scout_pool)]
        chosen.append(scout)
    for pair in ranked_risers:
        if len(chosen) >= max_pairs:
            break
        if pair not in chosen:
            chosen.append(pair)
    if len(chosen) < max_pairs and fallback:
        # Rotation is for sampling diversity ONLY, never an entry ranking.
        rotation_pool = [pair for _, pair in fallback[:20]]
        offset = (now // 60) % len(rotation_pool)
        rotated = rotation_pool[offset:] + rotation_pool[:offset]
        chosen.extend(rotated[:max_pairs-len(chosen)])
    return dict(empty,
        status=("M03_MICRO_DISCOVERY_PAIRS_SELECTED"
                if chosen else empty["status"]),
        pairs=chosen, fresh_liquid_pair_count=len(rising)+len(fallback),
        weak_price_rise_pair_count=len(rising),
        selection_policy=(
            "SOURCE_FRESH_RISING_LEADER_PLUS_EARLY_BELOW_ALERT_SCOUT_"
            "THEN_ROTATING_LIQUID_BASELINE_NO_M05_ALERT_DEPENDENCY"
        ),
    )


def inspect_original_m03_ws3_watch_readonly(
    *, db_path: Path = DEFAULT_DB, now_epoch: int | None = None,
) -> dict:
    """Read the existing original M03 WS3 minute archive, without network I/O.

    WATCH-only, since summary-24h updates cannot establish a real 1m candle,
    aggressor BUY/SELL, depth, complete universe, or an independent G1 source.
    """
    now = int(time.time()) if now_epoch is None else now_epoch
    if type(now) is not int or now <= 0:
        raise ValueError("M05_WS3_RADAR_INVALID_CLOCK")
    # Accept relative paths from operator/read-only CLI callers, but do
    # not dereference the final file symlink before ownership validation.
    path = Path(db_path).expanduser()
    if not _owned_private_regular(path):
        return {"status": "M05_WS3_SOURCE_UNTRUSTED", "candidates": []}
    # sqlite URI paths must be absolute; .as_uri() raises ValueError on
    # relative paths. .absolute() is intentionally not .resolve() to keep
    # final-component symlink rejection meaningful.
    path = path.absolute()
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='m03_ws_summary_watch_minute'"
            ).fetchone()
            if not exists:
                return {"status": "M05_WS3_SOURCE_ABSENT", "candidates": []}
            rows = db.execute(
                "SELECT pair,minute_epoch,exchange_epoch,last_idr,"
                "rolling_24h_idr,public_offset "
                "FROM m03_ws_summary_watch_minute "
                "WHERE minute_epoch BETWEEN ? AND ? "
                "ORDER BY minute_epoch DESC LIMIT 2000",
                (now - 420, now),
            ).fetchall()
            micro_exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='m03_ws_microstructure_watch_minute'"
            ).fetchone()
            micro_rows = db.execute(
                "SELECT pair,minute_epoch,observed_local_epoch,"
                "trade_frames,source_buy_count,source_sell_count,"
                "source_buy_idr,source_sell_idr,last_trade_epoch,"
                "trade_offset,trade_gap_observed,trade_truncated,"
                "book_offset,book_local_epoch,best_bid_idr,best_ask_idr,"
                "spread_bps,bid_depth_top5_idr,ask_depth_top5_idr,"
                "book_imbalance_top5_ratio,source_malformed "
                "FROM m03_ws_microstructure_watch_minute "
                "WHERE minute_epoch BETWEEN ? AND ? "
                "ORDER BY minute_epoch DESC LIMIT 200",
                (now - 240, now),
            ).fetchall() if micro_exists else []
    except (sqlite3.Error, OSError, ValueError):
        return {"status": "M05_WS3_SOURCE_QUERY_UNAVAILABLE",
                "candidates": []}

    micro_by_pair = {}
    for row in micro_rows:
        if (type(row[0]) is str and PAIR_ID.fullmatch(row[0])
                and row[0] not in micro_by_pair):
            micro_by_pair[row[0]] = row
    by_pair = defaultdict(list)
    for pair, minute, exchange, raw_price, raw_turnover, offset in rows:
        if (type(pair) is not str or not PAIR_ID.fullmatch(pair)
                or pair in MAIN_EXCLUDED_PAIRS
                or type(minute) is not int or minute % 60 != 0
                or type(exchange) is not int or type(offset) is not int
                or offset < 0
                # M03 buckets by LOCAL insertion minute, while exchange
                # timestamps may occur DURING that minute or up to 90s
                # before it. Never require exchange <= minute start.
                or not -90 <= exchange - minute < 60
                or exchange > now):
            continue
        price = _finite_number(raw_price, positive=True)
        turnover = _finite_number(raw_turnover, positive=True)
        if price is None or turnover is None:
            continue
        by_pair[pair].append((minute, exchange, price, turnover, offset))

    candidates = []
    micro_prealert_candidates = []
    liquid_watch_candidates = []
    for pair, samples in by_pair.items():
        latest = samples[0]
        if (not 0 <= now - latest[0] <= WS3_MAX_AGE_SECONDS
                or not 0 <= now - latest[1] <= WS3_MAX_AGE_SECONDS
                or latest[3] < MIN_TURNOVER_IDR):
            continue
        reference = next((
            old for old in samples[1:]
            if 60 <= latest[0] - old[0] <= 300
            and old[1] < latest[1]
        ), None)
        # Independent WATCH universe: same original fresh WS3 rows, not
        # conditional on a positive short step, momentum rank or AI/G1.
        # No reference is not made up; timeframe coverage is checked in M05.
        if latest[3] >= Decimal("400000000"):
            short_rise = (latest[2] / reference[2] - 1) * 100 if reference else None
            liquid_watch_candidates.append({
                "pair": pair, "observed_epoch": latest[1],
                "reference_epoch": reference[1] if reference else None,
                "observed_rise_pct": format(short_rise, ".4f") if short_rise is not None else None,
                "latest_price_idr": format(latest[2], "f"),
                "volume_24h_idr": format(latest[3], "f"),
                "source": "INDODAX_PUBLIC_WS3_SUMMARY_24H",
                "microstructure_observation": _ws3_micro_observation(
                    micro_by_pair.get(pair), now=now, source_minute=latest[0]),
                "g1_verified": False, "execution_allowed": False,
                "watch_path": "INDEPENDENT_FOUR_GREEN",
            })
        if reference is None:
            continue
        rise = (latest[2] / reference[2] - 1) * 100
        if not rise.is_finite() or rise <= 0 or rise > Decimal("500"):
            continue
        delta = latest[3] - reference[3]
        review = _ws3_price_activity_review(samples, latest, reference)
        review["microstructure_observation"] = _ws3_micro_observation(
            micro_by_pair.get(pair), now=now, source_minute=latest[0],
        )
        # A micro-backed PREALERT is strictly informational and does not
        # relax the original M05 Telegram WATCH publication thresholds.
        alert_threshold_met = (
            rise >= WS3_MIN_PRICE_RISE_PCT or
            review["momentum_phase"] == "EARLY_ACCELERATION_WATCH"
        )
        candidate = {
            "pair": pair,
            "reference_epoch": reference[1],
            "observed_epoch": latest[1],
            "source_elapsed_seconds": latest[1] - reference[1],
            "reference_price_idr": format(reference[2], "f"),
            "latest_price_idr": format(latest[2], "f"),
            "observed_rise_pct": format(rise, ".4f"),
            "volume_24h_idr": format(latest[3], "f"),
            "volume_24h_net_change_idr": format(delta, "f"),
            "source": "INDODAX_PUBLIC_WS3_SUMMARY_24H",
            "closed_1m_candle_verified": False,
            "trade_direction_verified": False,
            "bid_ask_spread_verified": False,
            "g1_verified": False,
            "execution_allowed": False,
            **review,
        }
        candidate["microstructure_priority_rank"] = (
            _ws3_observation_priority(candidate)
        )
        micro = candidate["microstructure_observation"]
        if (Decimal("0.05") <= rise <= Decimal("8")
                and micro.get("status") == "PARTIAL_SOURCE_OBSERVATION"
                and micro.get("source_malformed") is not True
                and (micro.get("trade_activity_observed") is True
                     or micro.get("orderbook_snapshot_observed") is True)):
            # Source backed, partial capture only, no trading authority.
            candidate["micro_prealert_observation"] = True
            candidate["m05_telegram_watch_threshold_met"] = (
                alert_threshold_met
            )
            micro_prealert_candidates.append(candidate)
        if alert_threshold_met:
            candidates.append(candidate)
    micro_prealert_candidates.sort(key=lambda x: (
        0 if (x["microstructure_observation"].get("trade_activity_observed")
              and x["microstructure_observation"].get(
                  "orderbook_snapshot_observed")) else 1,
        x["momentum_priority_rank"],
        abs(Decimal(x["observed_rise_pct"]) - Decimal("0.35")),
        x["pair"],
    ))
    candidates.sort(key=lambda x: (
        x["momentum_priority_rank"],
        x["microstructure_priority_rank"],
        -Decimal(x["observed_rise_pct"]),
        -Decimal(x["volume_24h_net_change_idr"]),
        x["pair"],
    ))
    return {
        "status": "M03_WS3_WATCH_RADAR_OBSERVED_UNATTESTED",
        "candidates": candidates[:5],
        "quality_reviewed_candidate_count": len(candidates),
        "ranked_review_shortlist_top40": candidates[:40],
        # Bounded by the original 2000-row snapshot read, no extra network.
        # No top40 momentum cut on the independent WATCH universe.
        "liquid_watch_candidates": sorted(liquid_watch_candidates, key=lambda c: (
            -Decimal(c["volume_24h_idr"]), c["pair"])),
        "micro_prealert_candidates_top40": micro_prealert_candidates[:40],
        "micro_prealert_observed_count": len(micro_prealert_candidates),
        "micro_prealert_is_owner_telegram_alert": False,
        "source_authenticated_for_g1": False,
        "three_ai_role_responses_observed": False,
        "owner_order_specific_final_verified": False,
        "execution_allowed": False,
    }


def _idr_compact_id(value: str) -> str:
    """Short Indonesian Rupiah rendering for narrow Telegram screens."""
    number = Decimal(value)
    amount = abs(number)
    for divisor, suffix in (
        (Decimal("1000000000"), "miliar"),
        (Decimal("1000000"), "jt"),
        (Decimal("1000"), "rb"),
    ):
        if amount >= divisor:
            short = (number / divisor).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            return "Rp" + _idr_number(short) + " " + suffix
    return _idr_price(value)


_WS3_PHASE_ID = {
    "EARLY_ACCELERATION_WATCH": "Percepatan awal, perlu konfirmasi",
    "EXTENDED_MOVE_REVIEW": "Lonjakan besar, waspadai harga terlambat",
    "PRICE_RISE_ROLLING_DELTA_POSITIVE": "Harga naik, nilai 24j bertambah",
    "PRICE_RISE_ACTIVITY_UNCONFIRMED": "Harga naik, aktivitas belum pasti",
}


def _ws3_micro_notice(candidate: dict) -> str:
    micro = candidate.get("microstructure_observation")
    if not isinstance(micro, dict) or micro.get("status") == "NOT_OBSERVED":
        return "🔬 Trade/order book: belum teramati"
    if micro.get("status") != "PARTIAL_SOURCE_OBSERVATION":
        return "🔬 Trade/order book: belum terverifikasi"
    parts = []
    if micro.get("trade_activity_observed"):
        parts.append("trade " + str(micro.get("captured_trade_count", "?")))
    if micro.get("orderbook_snapshot_observed"):
        parts.append("book parsial")
    if not parts:
        return "🔬 Trade/order book: belum teramati"
    return "🔬 " + " · ".join(parts) + " (cakupan tidak lengkap)"


def format_owner_ws3_radar_compact(candidate: dict) -> str:
    """Ringkas untuk ponsel; hanya WATCH WS3, bukan keputusan BUY/AI/G1."""
    wib = ZoneInfo("Asia/Jakarta")
    first = datetime.fromtimestamp(
        candidate["reference_epoch"], wib
    ).strftime("%H:%M:%S")
    last = datetime.fromtimestamp(
        candidate["observed_epoch"], wib
    ).strftime("%H:%M:%S")
    pair = candidate["pair"].upper().replace("_IDR", "/IDR")
    change = Decimal(candidate["observed_rise_pct"])
    direction = "🔺" if change > 0 else "🔻" if change < 0 else "➖"
    turnover_delta = Decimal(candidate["volume_24h_net_change_idr"])
    delta_sign = "+" if turnover_delta > 0 else ""
    phase = _WS3_PHASE_ID.get(
        candidate["momentum_phase"], "Aktivitas perlu ditinjau"
    )
    samples = candidate["compared_source_minute_samples"]
    return "\n".join((
        f"📡 WS3 WATCH | {pair}",
        f"{direction} {_pct(candidate['observed_rise_pct'])} · {first} → {last} WIB",
        f"💰 {_idr_price(candidate['reference_price_idr'])}"
        f" → {_idr_price(candidate['latest_price_idr'])}",
        "📊 Nilai 24j " + _idr_compact_id(candidate["volume_24h_idr"])
        + " · Δ " + delta_sign
        + _idr_compact_id(candidate["volume_24h_net_change_idr"]),
        "⚡ " + phase + " (" + str(samples) + " sampel)",
        _ws3_micro_notice(candidate),
        "⚠️ Bukan candle 1 menit. Δ nilai 24j bukan volume transaksi 1 menit atau tekanan beli.",
        "🔎 Pantauan publik saja. AI/G1, arus trade & kedalaman pasar belum diverifikasi. Bukan rekomendasi beli.",
    ))


def _claim_once(ledger, pair: str, observed_epoch: int,
                now_epoch: int, *, source_kind: str,
                global_spacing_seconds: int = GLOBAL_SPACING_SECONDS,
                watch_path: str = "MOMENTUM",
                green_at_claim: int | None = None) -> bool:
    """Durable at-most-once claim, plus bounded per-pair delivery audit.

    The ORIGINAL one-row-per-pair cooldown table is untouched. An additive
    private append-only event row is committed in the SAME transaction, before
    a Bot API send. An ACK is never inferred just from this claim.
    """
    if (source_kind not in ("WS3", "REST") or global_spacing_seconds not in
            (GLOBAL_SPACING_SECONDS, PREMIUM_GLOBAL_SPACING_SECONDS)
            or watch_path not in ("MOMENTUM", "INDEPENDENT_FOUR_GREEN")
            or (watch_path == "INDEPENDENT_FOUR_GREEN" and
                (source_kind != "WS3" or type(green_at_claim) is not int
                 or not 4 <= green_at_claim <= 8))
            or (green_at_claim is not None and
                (type(green_at_claim) is not int or not 4 <= green_at_claim <= 8))):
        return False
    dbpath = Path(ledger.db_path)
    if not _owned_private_regular(dbpath):
        return False
    try:
        with sqlite3.connect(dbpath, timeout=2) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS m05_radar_momentum_attempts (
                    pair TEXT PRIMARY KEY,
                    claimed_epoch INTEGER NOT NULL,
                    source_observed_epoch INTEGER NOT NULL
                )
            """)
            # Same existing owner SQLite; not a second sender or new ledger.
            # Never persist owner IDs, Telegram tokens or message text.
            db.execute("""
                CREATE TABLE IF NOT EXISTS m05_radar_delivery_events (
                    pair TEXT NOT NULL,
                    claimed_epoch INTEGER NOT NULL,
                    source_observed_epoch INTEGER NOT NULL,
                    source_kind TEXT NOT NULL
                        CHECK(source_kind IN ('WS3','REST')),
                    delivery_state TEXT NOT NULL
                        CHECK(delivery_state IN ('CLAIMED','ACKED','UNCERTAIN')),
                    ack_received_epoch INTEGER,
                    telegram_message_id INTEGER,
                    PRIMARY KEY(pair, claimed_epoch, source_observed_epoch),
                    CHECK(
                        (delivery_state='ACKED'
                         AND ack_received_epoch IS NOT NULL
                         AND telegram_message_id > 0)
                        OR
                        (delivery_state!='ACKED'
                         AND ack_received_epoch IS NULL
                         AND telegram_message_id IS NULL)
                    )
                )
            """)
            columns = {r[1] for r in db.execute(
                "PRAGMA table_info(m05_radar_delivery_events)")}
            if "watch_path" not in columns:
                db.execute("ALTER TABLE m05_radar_delivery_events "
                           "ADD COLUMN watch_path TEXT NOT NULL DEFAULT 'MOMENTUM'")
            if "green_at_claim" not in columns:
                db.execute("ALTER TABLE m05_radar_delivery_events "
                           "ADD COLUMN green_at_claim INTEGER")
            db.execute("CREATE INDEX IF NOT EXISTS "
                       "m05_radar_delivery_events_claimed_idx ON "
                       "m05_radar_delivery_events(claimed_epoch)")
            if db.execute(
                "SELECT 1 FROM m05_radar_momentum_attempts "
                "WHERE claimed_epoch > ? LIMIT 1",
                (now_epoch - global_spacing_seconds,),
            ).fetchone():
                return False
            row = db.execute(
                "SELECT claimed_epoch,source_observed_epoch "
                "FROM m05_radar_momentum_attempts WHERE pair=?", (pair,),
            ).fetchone()
            if row and (
                row[0] >= now_epoch - PAIR_COOLDOWN_SECONDS
                or row[0] >= now_epoch
                or row[1] >= observed_epoch
            ):
                return False
            db.execute("""
                INSERT INTO m05_radar_momentum_attempts(
                    pair,claimed_epoch,source_observed_epoch
                ) VALUES(?,?,?)
                ON CONFLICT(pair) DO UPDATE SET
                    claimed_epoch=excluded.claimed_epoch,
                    source_observed_epoch=excluded.source_observed_epoch
            """, (pair, now_epoch, observed_epoch))
            db.execute("""
                INSERT INTO m05_radar_delivery_events(
                    pair,claimed_epoch,source_observed_epoch,source_kind,
                    delivery_state,ack_received_epoch,telegram_message_id,
                    watch_path,green_at_claim
                ) VALUES(?,?,?,?, 'CLAIMED',NULL,NULL,?,?)
            """, (pair, now_epoch, observed_epoch, source_kind,watch_path,green_at_claim))
            # Max 30 days per-pair outcome evidence, preserving bounded disk.
            db.execute(
                "DELETE FROM m05_radar_delivery_events WHERE claimed_epoch < ?",
                (now_epoch - 30 * 86400,),
            )
        return True
    except (sqlite3.Error, OSError):
        return False


def _finalize_radar_delivery_event(
    ledger, *, pair: str, claim_epoch: int, source_observed_epoch: int,
    state: str, message_id: int | None = None,
) -> bool:
    """Set exact original claim ACK/UNCERTAIN without changing sender result.

    A failed local audit write must NOT erase a successful Bot API ACK or
    provoke a second potentially duplicate Telegram send.
    """
    if state not in ("ACKED", "UNCERTAIN"):
        return False
    if state == "ACKED" and (type(message_id) is not int or message_id <= 0):
        return False
    if state == "UNCERTAIN" and message_id is not None:
        return False
    path = Path(ledger.db_path)
    if not _owned_private_regular(path):
        return False
    try:
        with sqlite3.connect(path, timeout=2) as db:
            if state == "ACKED":
                cursor = db.execute("""
                    UPDATE m05_radar_delivery_events
                    SET delivery_state='ACKED',
                        ack_received_epoch=?, telegram_message_id=?
                    WHERE pair=? AND claimed_epoch=? AND
                          source_observed_epoch=? AND delivery_state='CLAIMED'
                """, (int(time.time()), message_id, pair, claim_epoch,
                      source_observed_epoch))
            else:
                cursor = db.execute("""
                    UPDATE m05_radar_delivery_events
                    SET delivery_state='UNCERTAIN'
                    WHERE pair=? AND claimed_epoch=? AND
                          source_observed_epoch=? AND delivery_state='CLAIMED'
                """, (pair, claim_epoch, source_observed_epoch))
            return cursor.rowcount == 1
    except (sqlite3.Error, OSError):
        return False



def _idr_number(value: Decimal) -> str:
    """Readable Indonesian separators, preserving sub-rupiah precision."""
    raw = format(value, "f")
    negative = raw.startswith("-")
    if negative:
        raw = raw[1:]
    integer, dot, fraction = raw.partition(".")
    grouped = format(int(integer), ",").replace(",", ".")
    fraction = fraction.rstrip("0")
    return ("-" if negative else "") + grouped + (
        "," + fraction if dot and fraction else ""
    )


def _idr_price(value: str) -> str:
    return "Rp" + _idr_number(Decimal(value))


def _idr_compact(value: str) -> str:
    """Display approximate money units, never alter source or scoring."""
    amount = Decimal(value)
    magnitude = abs(amount)
    if magnitude >= Decimal("1000000000"):
        return ("Rp" + _idr_number(
            (amount / Decimal("1000000000")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
        ) + " billion")
    if magnitude >= Decimal("1000000"):
        return ("Rp" + _idr_number(
            (amount / Decimal("1000000")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
        ) + " million")
    return _idr_price(value)


def _pct(value: str) -> str:
    number = Decimal(value).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return ("+" if number > 0 else "") + format(number, ".2f").replace(".", ",") + "%"


def format_owner_radar_compact(candidate: dict) -> str:
    """Phone-sized PUBLIC WATCH notice, same source and safety semantics.

    Price is sample-to-sample, NEVER a fabricated candle. Rolling 24h ticker
    change is NOT actual short-window executed turnover / BUY-SELL pressure.
    No recommendation, provider/G1 promotion or executable proposal.
    """
    wib = ZoneInfo("Asia/Jakarta")
    from_time = datetime.fromtimestamp(
        candidate["reference_epoch"], wib
    ).strftime("%H:%M")
    to_time = datetime.fromtimestamp(
        candidate["observed_epoch"], wib
    ).strftime("%H:%M")
    price_pct = Decimal(candidate["observed_rise_pct"])
    direction = "🔺" if price_pct > 0 else "🔻" if price_pct < 0 else "➖"
    pair = candidate["pair"].upper().replace("_IDR", "/IDR")
    if candidate["casebook_early_watch_observed"]:
        label = "EARLY_WATCH (historical)"
    elif candidate["price_watch"] and candidate["volume_watch"]:
        label = "PRICE + 24H VOLUME"
    elif candidate["price_watch"]:
        label = "PRICE MOMENTUM"
    else:
        label = "24H VOLUME CHANGE"

    lines = [
        f"📡 RADAR | {pair} · {label}",
        f"{direction} Price {from_time}→{to_time}: {_pct(candidate['observed_rise_pct'])}",
        f"💰 {_idr_price(candidate['reference_price_idr'])}"
        f" → {_idr_price(candidate['latest_price_idr'])}",
        "📊 24h volume: " + _idr_compact(candidate["volume_24h_idr"]),
    ]
    if candidate["volume_watch"]:
        mins = candidate["volume_comparison_seconds"] // 60
        lines.extend((
            f"📈 Δ 24h volume ({mins}m): "
            f"+{_idr_compact(candidate['volume_24h_net_change_idr'])}"
            f" ({_pct(candidate['volume_24h_net_change_pct'])})",
            "⚠️ The rolling 24h volume delta is NOT 1m executed turnover or BUY/SELL pressure.",
        ))
    if candidate["missing_intermediate_minute_samples"]:
        lines.append("⚠️ Missing intermediate minute samples; NOT a closed 1m candle return.")
    lines.append(
         "🔎 PUBLIC WATCH, NOT A BUY RECOMMENDATION. "
        "AI/G1 and maintenance/suspension status UNVERIFIED."
    )
    return "\n".join(lines)



def send_one_owner_radar_momentum_nonlive(
    *, api, ledger, now_epoch: int | None = None,
    db_path: Path = DEFAULT_DB, ws3_enabled: bool = False,
    priority_alert_enabled: bool = False,
) -> dict:
    """Existing M05 private Bot API sender, no callback buttons, no order."""
    now = int(time.time()) if now_epoch is None else now_epoch
    from .owner_control import OwnerDecisionLedger
    if type(now) is not int or now <= 0:
        raise ValueError("M05_RADAR_CLOCK_INVALID")
    if not isinstance(ledger, OwnerDecisionLedger):
        return {"status": "M05_RADAR_OWNER_LEDGER_UNVERIFIED",
                "messages_sent": 0}
    if type(ws3_enabled) is not bool:
        raise ValueError("M05_WS3_RADAR_MODE_INVALID")
    if type(priority_alert_enabled) is not bool:
        raise ValueError("M05_RADAR_PRIORITY_MODE_INVALID")
    snap = inspect_original_m03_radar_readonly(
        db_path=db_path, now_epoch=now,
    )

    premium = os.environ.get("M05_RADAR_PREMIUM_LAYOUT_GO") == "YES"

    def priority_only(snapshot: dict) -> dict:
        # Optional opt-in. Keep original 0.35% early scout / 150m discovery
        # intact in M03 and M04; filter ONLY the M05 owner alert publication.
        if not priority_alert_enabled or not snapshot.get("candidates"):
            if premium and isinstance(snapshot.get("ranked_review_shortlist_top40"), list):
                return dict(snapshot, candidates=snapshot["ranked_review_shortlist_top40"][:40])
            return snapshot
        from .m08_strategy_a_nonlive import assess_ws3_priority_alert_offline
        eligible = []
        # Use the original WS3 top40 BEFORE top5 publication truncation.
        # Otherwise five early scouts can mask a valid 1.5%/400m alert.
        ranked = snapshot.get("ranked_review_shortlist_top40")
        pool = ranked if isinstance(ranked, list) else snapshot["candidates"]
        for row in pool:
            try:
                verdict = assess_ws3_priority_alert_offline(
                    rise_pct=Decimal(row["observed_rise_pct"]),
                    rolling_24h_idr=Decimal(row["volume_24h_idr"]),
                    early_acceleration=(
                        row.get("momentum_phase") == "EARLY_ACCELERATION_WATCH"),
                )
                if verdict["priority_alert_eligible"]:
                    eligible.append(row)
            except (KeyError, InvalidOperation, TypeError, ValueError):
                # No fabricated positive eligibility for malformed sources.
                continue
        return dict(snapshot, candidates=eligible[:40 if premium else 5])

    snap = priority_only(snap)
    # Premium checks both original cached source families, even when REST
    # has candidates that fail the display gate or the durable claim.
    # WS3 first in the WS3-enabled premium runtime; each card uses its own
    # source and never combines REST price history with WS3 ticker history.
    snapshots = [snap]
    independent = []
    if ws3_enabled and (premium or not snap.get("candidates")):
        ws3 = inspect_original_m03_ws3_watch_readonly(
            db_path=db_path, now_epoch=now,
        )
        if ws3["status"] == "M03_WS3_WATCH_RADAR_OBSERVED_UNATTESTED":
            filtered = priority_only(ws3)
            snapshots = [filtered, snap] if premium else [filtered]
            if premium:
                independent = ws3.get("liquid_watch_candidates", [])
    valid = [x for x in snapshots if x["status"] in (
        "M03_ORIGINAL_PUBLIC_RADAR_OBSERVED_UNATTESTED",
        "M03_WS3_WATCH_RADAR_OBSERVED_UNATTESTED")]
    if not valid:
        return {"status": "M05_RADAR_ORIGINAL_SOURCE_UNAVAILABLE",
                "messages_sent": 0}
    pool = [(c, "WS3" if x["status"] ==
             "M03_WS3_WATCH_RADAR_OBSERVED_UNATTESTED" else "REST")
            for x in valid for c in x["candidates"]]
    # Preserve original momentum priority, then examine all remaining
    # fresh liquid WS3 candidates using the same rendered 4/8 gate.
    # A pair already in the original WS3 path has identical source rows.
    if premium:
        present = {c["pair"] for c, kind in pool if kind == "WS3"}
        pool.extend((c, "WS3") for c in independent if c["pair"] not in present)
    if not pool:
        return {"status": "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH",
                "messages_sent": 0}
    eligible_count = 0
    for candidate, source_kind in pool:
        parse_mode = None
        green_at_claim = None
        if premium:
            # New owner publication gate BEFORE durable claim. No legacy
            # fallback may bypass the four-green rule on data/render failure.
            from .m05_pitiden_premium_layout import (
                DEFAULT_EVIDENCE_DB, format_pitiden_premium,
                read_price_references, read_closed_candles,
                read_native_previous_24h, build_timeframes,
                eligible_premium_watch, MIN_TURNOVER_24H, count_green,
            )
            evidence_path = DEFAULT_EVIDENCE_DB if Path(db_path) == DEFAULT_DB else None
            try:
                turnover = _finite_number(candidate.get("volume_24h_idr"))
                if turnover is None or turnover < MIN_TURNOVER_24H:
                    continue
                refs = read_price_references(
                    candidate, db_path=db_path, now_epoch=now,
                    source_kind=source_kind, evidence_path=evidence_path,
                )
                candles = read_closed_candles(
                    candidate, db_path=db_path, now_epoch=now,
                )
                previous = (read_native_previous_24h(
                    candidate, evidence_path=evidence_path, now_epoch=now,
                ) if source_kind == "WS3" else None)
                rows = build_timeframes(candidate, refs, candles=candles,
                                        previous_24h=previous)
                if not eligible_premium_watch(candidate, rows):
                    continue
                green_at_claim = count_green(rows)
                message = format_pitiden_premium(
                    candidate, refs, source_kind=source_kind,
                    now_epoch=now, rows=rows,
                )
                parse_mode = "HTML"
            except (OSError, sqlite3.Error, ValueError, TypeError,
                    ArithmeticError, KeyError):
                continue
        else:
            message = (format_owner_ws3_radar_compact(candidate)
                       if source_kind == "WS3"
                       else format_owner_radar_compact(candidate))
        eligible_count += 1
        if not _claim_once(
            ledger, candidate["pair"], candidate["observed_epoch"], now,
            source_kind=source_kind,
            global_spacing_seconds=(PREMIUM_GLOBAL_SPACING_SECONDS if premium
                                    else GLOBAL_SPACING_SECONDS),
            watch_path=candidate.get("watch_path", "MOMENTUM"),
            green_at_claim=green_at_claim,
        ):
            continue
        # Claim is durable BEFORE send; uncertainty must never replay it.
        # The added audit row is informational, never an order authorization.
        try:
            msg = api.call("sendMessage", {
                "chat_id": ledger.owner_user_id,
                "text": message,
                **({"parse_mode": parse_mode} if parse_mode else {}),
            })
            if (not isinstance(msg, dict)
                    or type(msg.get("message_id")) is not int
                    or msg["message_id"] <= 0
                    or not isinstance(msg.get("chat"), dict)
                    or msg["chat"].get("id") != ledger.owner_user_id
                    or msg["chat"].get("type") != "private"):
                recorded = _finalize_radar_delivery_event(
                    ledger, pair=candidate["pair"], claim_epoch=now,
                    source_observed_epoch=candidate["observed_epoch"],
                    state="UNCERTAIN",
                )
                return {
                    "status": "M05_RADAR_DELIVERY_UNCERTAIN_NO_RETRY",
                    "messages_sent": 0,
                    "pair_delivery_audit_persisted": recorded,
                }
        except Exception:
            # Optional radar must not stop the original owner receiver.
            recorded = _finalize_radar_delivery_event(
                ledger, pair=candidate["pair"], claim_epoch=now,
                source_observed_epoch=candidate["observed_epoch"],
                state="UNCERTAIN",
            )
            return {
                "status": "M05_RADAR_DELIVERY_UNCERTAIN_NO_RETRY",
                "messages_sent": 0,
                "pair_delivery_audit_persisted": recorded,
            }
        recorded = _finalize_radar_delivery_event(
            ledger, pair=candidate["pair"], claim_epoch=now,
            source_observed_epoch=candidate["observed_epoch"],
            state="ACKED", message_id=msg["message_id"],
        )
        return {
            "status": "M05_RADAR_OWNER_PRIVATE_NOTICE_SENT",
            "messages_sent": 1, "execution_allowed": False,
            "owner_final_decision": None,
            "pair_delivery_audit_persisted": recorded,
        }
    if premium and not eligible_count:
        return {"status": "M05_RADAR_PREMIUM_FOUR_GREEN_NOT_MET",
                "messages_sent": 0}
    return {"status": "M05_RADAR_THROTTLED",
            "messages_sent": 0}
