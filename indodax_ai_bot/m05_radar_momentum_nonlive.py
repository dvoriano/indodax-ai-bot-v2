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


def _claim_once(ledger, pair: str, observed_epoch: int,
                now_epoch: int) -> bool:
    """At-most-once owner notice attempt; no replay on Telegram uncertainty."""
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
            if db.execute(
                "SELECT 1 FROM m05_radar_momentum_attempts "
                "WHERE claimed_epoch > ? LIMIT 1",
                (now_epoch - GLOBAL_SPACING_SECONDS,),
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
        return True
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
    db_path: Path = DEFAULT_DB,
) -> dict:
    """Existing M05 private Bot API sender, no callback buttons, no order."""
    now = int(time.time()) if now_epoch is None else now_epoch
    from .owner_control import OwnerDecisionLedger
    if type(now) is not int or now <= 0:
        raise ValueError("M05_RADAR_CLOCK_INVALID")
    if not isinstance(ledger, OwnerDecisionLedger):
        return {"status": "M05_RADAR_OWNER_LEDGER_UNVERIFIED",
                "messages_sent": 0}
    snap = inspect_original_m03_radar_readonly(
        db_path=db_path, now_epoch=now,
    )
    if snap["status"] != "M03_ORIGINAL_PUBLIC_RADAR_OBSERVED_UNATTESTED":
        return {"status": "M05_RADAR_ORIGINAL_SOURCE_UNAVAILABLE",
                "messages_sent": 0}
    if not snap["candidates"]:
        return {"status": "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH",
                "messages_sent": 0}
    for candidate in snap["candidates"]:
        if not _claim_once(
            ledger, candidate["pair"], candidate["observed_epoch"], now
        ):
            continue
        message = format_owner_radar_compact(candidate)
        # Claim is durable BEFORE send; uncertainty must never replay it.
        try:
            msg = api.call("sendMessage", {
                "chat_id": ledger.owner_user_id,
                "text": message,
            })
            if (not isinstance(msg, dict)
                    or type(msg.get("message_id")) is not int
                    or msg["message_id"] <= 0
                    or not isinstance(msg.get("chat"), dict)
                    or msg["chat"].get("id") != ledger.owner_user_id
                    or msg["chat"].get("type") != "private"):
                return {
                    "status": "M05_RADAR_DELIVERY_UNCERTAIN_NO_RETRY",
                    "messages_sent": 0,
                }
        except Exception:
            # Optional radar must not stop the original owner receiver.
            return {
                "status": "M05_RADAR_DELIVERY_UNCERTAIN_NO_RETRY",
                "messages_sent": 0,
            }
        return {
            "status": "M05_RADAR_OWNER_PRIVATE_NOTICE_SENT",
            "messages_sent": 1, "execution_allowed": False,
            "owner_final_decision": None,
        }
    return {"status": "M05_RADAR_THROTTLED",
            "messages_sent": 0}
