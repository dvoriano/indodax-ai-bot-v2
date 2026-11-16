"""Compact PITIDEN WATCH card, from existing original ticker evidence only.

No network, new collector, order, provider, or G1. Nominal timeframe returns
use an actual nonfuture reference sample, not interpolated candles. Rolling
24h ticker turnover is NOT short-window executed volume.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from .m03_ws3_evidence_archive_nonlive import (
    DEFAULT_EVIDENCE_DB, _private_owned_regular,
)

FRAMES = (("1m", 60), ("5m", 300), ("10m", 600), ("15m", 900),
          ("30m", 1800), ("1h", 3600), ("24h", 86400))
WIB = ZoneInfo("Asia/Jakarta")
_LAYOUT = "PITIDEN_PREMIUM_WATCH_V1"


def _decimal(raw, *, positive=False):
    if type(raw) not in (int, str, Decimal) or len(str(raw)) > 72:
        return None
    try:
        n = Decimal(raw)
        if (not n.is_finite() or abs(n.adjusted()) > 20
                or (n <= 0 if positive else n < 0)):
            return None
        return n
    except (InvalidOperation, ValueError):
        return None


def _number(n):
    if n is None:
        return "N/A"
    whole, _, frac = format(n, "f").partition(".")
    return format(int(whole), ",").replace(",", ".") + (
        "," + frac.rstrip("0") if frac.rstrip("0") else ""
    )


def _volume(n):
    if n is None:
        return "N/A"
    for unit, suffix in ((10**12, "T"), (10**9, "M"), (10**6, "jt"),
                         (1000, "rb")):
        if n >= unit:
            return _number((n/Decimal(unit)).quantize(
                Decimal(".01"), rounding=ROUND_HALF_UP)) + suffix
    return _number(n.quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def _read_rows(path, sql, params):
    """One bounded read; missing/unavailable evidence must not stop WATCH."""
    try:
        with closing(sqlite3.connect(
            Path(path).absolute().as_uri()+"?mode=ro", uri=True, timeout=.15
        )) as db:
            db.execute("PRAGMA query_only=ON")
            return db.execute(sql, params).fetchall()
    except (OSError, sqlite3.Error, ValueError):
        return []


def read_price_references(candidate, *, db_path, now_epoch,
                          source_kind, evidence_path=None):
    """Existing single-pair indexed reads only. Source families never mixed."""
    from .m05_radar_momentum_nonlive import _owned_private_regular

    pair = candidate["pair"]
    end = candidate["observed_epoch"]
    if (type(pair) is not str or re.fullmatch(r"[a-z0-9]+_idr", pair) is None
            or type(end) is not int or not 0 < end <= now_epoch
            or source_kind not in ("WS3", "REST")):
        return []
    start = end - 86400 - 150
    rows = []
    if _owned_private_regular(Path(db_path)):
        if source_kind == "WS3":
            # Source minute and exchange timestamp are distinct. This is
            # the same already persisted local source, never a new request.
            rows += _read_rows(db_path,
                "SELECT minute_epoch,exchange_epoch,last_idr,public_offset "
                "FROM m03_ws_summary_watch_minute WHERE pair=? "
                "AND minute_epoch BETWEEN ? AND ? AND exchange_epoch<=? "
                "ORDER BY minute_epoch DESC LIMIT 1500",
                (pair, start, now_epoch, end))
        else:
            rows += _read_rows(db_path,
                "SELECT minute_epoch,observed_epoch,last_idr,0 "
                "FROM public_ticker_minute_samples WHERE pair=? "
                "AND minute_epoch BETWEEN ? AND ? AND observed_epoch<=? "
                "ORDER BY minute_epoch DESC LIMIT 1500",
                (pair, start, now_epoch, end))
    if source_kind == "WS3" and evidence_path is not None:
        path = Path(evidence_path)
        try:
            private = _private_owned_regular(path)
        except OSError:
            private = False
        if private:
            rows += _read_rows(path,
                "SELECT p.minute_epoch,p.source_exchange_epoch,p.last_idr,"
                "p.public_offset FROM ws3_source_evidence p "
                "JOIN ws3_source_cycles c USING(minute_epoch) "
                "WHERE p.pair=? AND p.minute_epoch BETWEEN ? AND ? "
                "AND p.source_exchange_epoch<=? "
                "AND c.recorded_local_epoch<=? "
                "AND p.source_status IN ('ELIGIBLE_SOURCE_OBSERVATION',"
                "'BELOW_150M_VOLUME_FLOOR') "
                "ORDER BY p.minute_epoch DESC LIMIT 1500",
                (pair, start, now_epoch, end, now_epoch))
    # Reject conflicting prices for the same source epoch. Do not average.
    prices = {}
    conflicts = set()
    for minute, stamp, raw, offset in rows:
        if (type(minute) is not int or minute % 60
                or type(stamp) is not int or not 0 < stamp <= end
                or type(offset) is not int or offset < 0
                or not (-90 if source_kind == "WS3" else 0) <= stamp-minute < 60):
            continue
        p = _decimal(raw, positive=True)
        if p is None:
            continue
        if stamp in prices and prices[stamp] != p:
            conflicts.add(stamp)
        prices[stamp] = p
    return sorted((stamp, p) for stamp, p in prices.items()
                  if stamp not in conflicts)


def build_timeframes(candidate, references):
    """One anchor (latest observed source epoch) for all seven comparisons.

    Reference must be at or before target; accept <=15s older for 1m,
    <=30s for other intervals; mark approximations with ~. No interpolation.
    """
    end = candidate["observed_epoch"]
    current = _decimal(candidate["latest_price_idr"], positive=True)
    rows = []
    for label, seconds in FRAMES:
        target = end-seconds
        tolerance = 15 if seconds == 60 else 30
        matches = [(stamp, price) for stamp, price in references
                   if target-tolerance <= stamp <= target]
        ref = max(matches, default=None, key=lambda x: x[0])
        change = None
        if current is not None and ref is not None:
            change = (current/ref[1]-1)*100
        rows.append({
            "tf": label, "reference_price": ref[1] if ref else None,
            "reference_epoch": ref[0] if ref else None,
            "change": change, "approx": bool(ref and ref[0] != target),
            # No complete, independently reconciled short-window tape is
            # available in the original radar source. Never use rolling delta.
            "volume": (_decimal(candidate.get("volume_24h_idr"))
                       if seconds == 86400 else None),
        })
    return rows


def _direction(change):
    return "⚪" if change is None or change == 0 else (
        "🟢" if change > 0 else "🔴"
    )


def _percent(change):
    if change is None:
        return "N/A"
    # Avoid a positive/negative indicator accompanying rounded zero.
    rounded = change.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    if rounded == 0 and change != 0:
        return "+<0,01%" if change > 0 else "-<0,01%"
    return (("+" if rounded > 0 else "") +
            format(rounded, ".2f").replace(".", ",") + "%")


def _momentum(rows):
    short = [r["change"] for r in rows[:4] if r["change"] is not None]
    if len(short) < 2:
        return "N/A"
    if all(x > 0 for x in short):
        return "Rising"
    if all(x < 0 for x in short):
        return "Falling"
    return "Mixed" if any(x != 0 for x in short) else "Flat"


def format_pitiden_premium(candidate, references, *, source_kind, now_epoch):
    """Telegram HTML: only heading bold; preformatted compact columns.

    Watch score and volume acceleration remain unavailable until real
    transaction-window evidence and a validated score definition exist.
    """
    rows = build_timeframes(candidate, references)
    pair = candidate["pair"].upper().replace("_IDR", "/IDR")
    current = _number(_decimal(candidate["latest_price_idr"], positive=True))
    time = datetime.fromtimestamp(candidate["observed_epoch"], WIB).strftime("%H:%M:%S")
    table = ["TF    CHANGE      PRICE       VOL"]
    for row in rows:
        change = _percent(row["change"])
        label = row["tf"] + ("~" if row["approx"] else "")
        table.append(
            f"{_direction(row['change'])} {label:<4}{change:>9} "
            f"{_number(row['reference_price']):>10} "
            f"{_volume(row['volume']):>8}"
        )
    spread = None
    if source_kind == "REST":
        spread = _decimal(candidate.get("spread_bps"))
    elif source_kind == "WS3":
        micro = candidate.get("microstructure_observation")
        if (isinstance(micro, dict)
                and micro.get("status") == "PARTIAL_SOURCE_OBSERVATION"
                and micro.get("source_malformed") is False
                and micro.get("orderbook_snapshot_observed") is True
                and type(micro.get("orderbook_locally_observed_epoch")) is int
                and 0 <= now_epoch-micro["orderbook_locally_observed_epoch"] <= 90):
            spread = _decimal(micro.get("spread_bps"))
    spread_text = _percent(spread/100).lstrip("+") if spread is not None else "N/A"
    # Keep the actual existing detector comparison visible even when all
    # requested nominal references are missing. This is a sample comparison.
    ref = candidate["reference_epoch"]
    trigger = Decimal(candidate["observed_rise_pct"])
    elapsed = candidate["observed_epoch"] - ref
    context = f"Trigger {_percent(trigger)} / {elapsed//60}m{elapsed%60:02d}s"
    lines = [
        f"📡 <b>{escape(pair)} · Rp{escape(current)}</b>",
        "<pre>" + escape("\n".join(table)) + "</pre>",
        escape(f"📈 {_momentum(rows)} · Vol accel N/A · Spread {spread_text}"),
        "👀 WATCH · Score N/A",
        escape(f"{context} · {time} WIB"),
        "<i>~ approx · Price ref · Vol Rp (24h rolling)</i>",
        "<i>AI/G1 unverified · Watch only · Not BUY</i>",
    ]
    return "\n".join(lines)
