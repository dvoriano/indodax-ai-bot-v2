"""Compact PITIDEN WATCH card, from existing original ticker evidence only.

No network, new collector, order, provider, or G1. Completed WIB intervals prefer existing locally validated public OHLC.
Ticker-only endpoint observations are explicitly approximate, never candles.
Rolling 24h turnover is NOT intraday executed IDR volume.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
from html import escape
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from .m03_ws3_evidence_archive_nonlive import (
    DEFAULT_EVIDENCE_DB, _private_owned_regular,
)

FRAMES = (("1m", 60), ("5m", 300), ("10m", 600), ("15m", 900),
          ("30m", 1800), ("1h", 3600), ("1D", 86400))
WIB = ZoneInfo("Asia/Jakarta")
_LAYOUT = "PITIDEN_PREMIUM_WATCH_V2_WIB"


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


def _midnight_wib(epoch):
    local = datetime.fromtimestamp(epoch, WIB)
    return int(local.replace(hour=0, minute=0, second=0,
                             microsecond=0).timestamp())


def interval_windows(end):
    """Six last completed WIB-aligned buckets, then current local day."""
    midnight = _midnight_wib(end)
    for label, seconds in FRAMES:
        if label == "1D":
            yield label, midnight, end
        else:
            closed_end = midnight + ((end-midnight)//seconds)*seconds
            yield label, closed_end-seconds, closed_end


def read_closed_candles(candidate, *, db_path, now_epoch):
    """Original OHLC archive only, no request or archive construction.

    Local row hashes and a qualifying recorded batch bound acceptance;
    this is public research data, not independent G1 or tape attestation.
    """
    from .m05_radar_momentum_nonlive import _owned_private_regular

    pair, end = candidate["pair"], candidate["observed_epoch"]
    if (type(pair) is not str or re.fullmatch(r"[a-z0-9]+_idr", pair) is None
            or type(end) is not int or not 0 < end <= now_epoch
            or not _owned_private_regular(Path(db_path))):
        return {}
    first = min(start for _, start, _ in interval_windows(end))
    rows = _read_rows(
        db_path,
        "SELECT c.start_epoch,c.open,c.high,c.low,c.close,c.volume,c.row_digest "
        "FROM public_ohlc_1m c WHERE c.pair=? AND c.start_epoch>=? "
        "AND c.start_epoch+60<=? AND EXISTS ("
        "SELECT 1 FROM public_ohlc_batches b WHERE b.pair=c.pair "
        "AND b.from_epoch<=c.start_epoch AND b.to_epoch>=c.start_epoch+60 "
        "AND b.observed_epoch<=? AND b.to_epoch<=b.observed_epoch "
        "AND b.to_epoch<=b.exchange_epoch "
        "AND ABS(b.observed_epoch-b.exchange_epoch)<=30 "
        "AND b.count BETWEEN 90 AND 360 "
        "AND b.to_epoch-b.from_epoch=60*b.count "
        "AND b.provenance='INDODAX_PUBLIC_OHLC_V2_UNATTESTED') "
        "ORDER BY c.start_epoch LIMIT 1501",
        (pair, first, end, now_epoch),
    )
    if len(rows) > 1500:
        return {}
    candles = {}
    for stamp, op, hi, lo, cl, vol, digest in rows:
        if (type(stamp) is not int or stamp % 60 or stamp < first
                or stamp+60 > end
                or any(type(x) is not str or len(x)>72
                       for x in (op, hi, lo, cl, vol, digest))):
            continue
        vals = [_decimal(x, positive=True) for x in (op, hi, lo, cl)]
        volume = _decimal(vol)
        if any(x is None for x in vals) or volume is None:
            continue
        o, h, l, c = vals
        if l > min(o,c) or h < max(o,c):
            continue
        expected = sha256(",".join((str(stamp),op,hi,lo,cl,vol))
                          .encode("utf-8")).hexdigest()
        if digest != expected:
            continue
        candles[stamp] = (o,c)
    return candles


def build_timeframes(candidate, references, *, candles=None):
    """Completed interval Open -> Close; 1D Open -> latest ticker.

    Prefer complete cached minute candles. Otherwise use distinct near-edge
    samples inside the interval only, always marked approximate. No nearest
    future sample, cross-midnight substitute, or invented zero volume.
    """
    end = candidate["observed_epoch"]
    current = _decimal(candidate["latest_price_idr"], positive=True)
    candles = candles or {}
    rows = []
    for label, start, stop in interval_windows(end):
        daily = label == "1D"
        first = candles.get(start)
        last = candles.get(stop-60) if not daily else None
        opening = closing_price = None
        approx = False
        if daily:
            closing_price = current
            if first is not None:
                opening = first[0]
            elif end > start:
                samples = [(t,p) for t,p in references
                           if start <= t <= min(start+30,end-1)]
                if samples:
                    opening = min(samples, key=lambda x:x[0])[1]
                    approx = True
        elif first is not None and last is not None and all(
            t in candles for t in range(start,stop,60)
        ):
            opening, closing_price = first[0], last[1]
        else:
            tolerance = 15 if label == "1m" else 30
            samples = [(t,p) for t,p in references if start <= t < stop]
            left = [(t,p) for t,p in samples if t-start <= tolerance]
            right = [(t,p) for t,p in samples if stop-1-t <= tolerance]
            op = min(left,default=None,key=lambda x:x[0])
            cl = max(right,default=None,key=lambda x:x[0])
            if op and cl and op[0] < cl[0]:
                opening, closing_price = op[1],cl[1]
                approx = True
            elif last is not None:
                # A genuine last cached candle close is useful even if the
                # rest of the interval is incomplete. Do not infer change.
                closing_price = last[1]
            elif cl is not None:
                closing_price = cl[1]
                approx = True
        change = ((closing_price/opening-1)*100
                  if opening is not None and closing_price is not None
                  and stop>start else None)
        rows.append({
            "tf":label, "start_epoch":start, "end_epoch":stop,
            "opening_price":opening, "close_price":closing_price,
            "change":change, "approx":approx,
            # Archived OHLC volume's IDR denomination is not established;
            # rolling-24h ticker turnover cannot be substituted for 1D.
            "volume":None,
        })
    return rows


def _direction(change):
    return "⚫" if change is None else "⚪" if change == 0 else (
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


def format_pitiden_premium(candidate, references, *, source_kind, now_epoch,
                           candles=None):
    """Telegram HTML: only heading bold; preformatted compact columns.

    Watch score and volume acceleration remain unavailable until real
    transaction-window evidence and a validated score definition exist.
    """
    rows = build_timeframes(candidate, references, candles=candles)
    pair = candidate["pair"].upper().replace("_IDR", "/IDR")
    current = _number(_decimal(candidate["latest_price_idr"], positive=True))
    time = datetime.fromtimestamp(candidate["observed_epoch"], WIB).strftime("%H:%M:%S")
    table = ["TF    CHANGE      CLOSE      VOL"]
    for row in rows:
        change = _percent(row["change"])
        label = row["tf"] + ("~" if row["approx"] else "")
        table.append(
            f"{_direction(row['change'])} {label:<4}{change:>9} "
            f"{_number(row['close_price']):>10} "
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
        "<i>~ sampled · 1D since 00:00 WIB · Vol Rp</i>",
        "<i>AI/G1 unverified · Watch only · Not BUY</i>",
    ]
    return "\n".join(lines)
