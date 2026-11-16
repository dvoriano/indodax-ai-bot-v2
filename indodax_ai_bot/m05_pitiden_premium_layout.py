"""Compact PITIDEN WATCH card, from existing original ticker evidence only.

No network, new collector, order, provider, or G1. Completed UTC-aligned intervals prefer existing locally validated public OHLC.
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
          ("30m", 1800), ("1h", 3600), ("4h", 14400), ("24h", 86400))
WIB = ZoneInfo("Asia/Jakarta")
_LAYOUT = "PITIDEN_PREMIUM_WATCH_V6_ALIGNED_4_GREEN"
MIN_GREEN = 4
MIN_TURNOVER_24H = Decimal("400000000")


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
    for unit, suffix in ((10**12, "T"), (10**9, " miliar"), (10**6, "jt"),
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


def interval_windows(end):
    """UTC-aligned completed buckets, then rolling 24h to source time."""
    for label, seconds in FRAMES:
        if label == "24h":
            yield label, end-seconds, end
        else:
            closed_end = end//seconds*seconds
            yield label, closed_end-seconds, closed_end


def read_native_previous_24h(candidate, *, evidence_path, now_epoch):
    """Match stored official WS3 prev24h to the current market observation."""
    if evidence_path is None:
        return None
    path = Path(evidence_path)
    try:
        if not _private_owned_regular(path):
            return None
    except OSError:
        return None
    end = candidate["observed_epoch"]
    current = _decimal(candidate["latest_price_idr"], positive=True)
    volume = _decimal(candidate.get("volume_24h_idr"))
    if current is None or volume is None:
        return None
    rows = _read_rows(path,
        "SELECT p.last_idr,p.rolling_24h_idr,p.previous_24h_idr "
        "FROM ws3_source_evidence p JOIN ws3_source_cycles c USING(minute_epoch) "
        "WHERE p.pair=? AND p.source_exchange_epoch=? "
        "AND p.minute_epoch BETWEEN ? AND ? "
        "AND p.public_offset>=0 AND c.recorded_local_epoch<=? "
        "AND c.recorded_local_epoch>=p.source_exchange_epoch "
        "AND p.source_status='ELIGIBLE_SOURCE_OBSERVATION' LIMIT 8",
        (candidate["pair"],end,end-60,end+90,now_epoch))
    values = set()
    for price,turnover,previous in rows:
        if (_decimal(price,positive=True)==current
                and _decimal(turnover)==volume):
            p = _decimal(previous,positive=True)
            if p is not None:
                values.add(p)
    return next(iter(values)) if len(values)==1 else None


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
    first = min(start for label, start, _ in interval_windows(end)
                if label != "24h")
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


def build_timeframes(candidate, references, *, candles=None, previous_24h=None):
    """Completed interval Open -> Close; rolling 24h -> latest ticker.

    Prefer complete cached minute candles. Otherwise use distinct near-edge
    samples near the interval edges, always marked approximate. No nearest
    future sample, cross-midnight substitute, or invented zero volume.
    """
    end = candidate["observed_epoch"]
    current = _decimal(candidate["latest_price_idr"], positive=True)
    candles = candles or {}
    rows = []
    for label, start, stop in interval_windows(end):
        daily = label == "24h"
        first = candles.get(start)
        last = candles.get(stop-60) if not daily else None
        opening = closing_price = None
        approx = False
        if daily:
            closing_price = current
            opening = _decimal(previous_24h,positive=True)
            if opening is None:
                # Same-source historic sample, not native market statistics.
                samples = [(t,p) for t,p in references
                           if start-30 <= t <= start]
                if samples:
                    opening = max(samples,key=lambda x:x[0])[1]
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
            # Original archive commonly has ONE quote near :49 per minute.
            # If an in-bucket open is unavailable (or is the same quote as
            # the close), use only a genuinely observed previous-boundary
            # sample within the same tolerance. This is a marked sample
            # change, NEVER a verified candle open or complete trade tape.
            if cl and (op is None or op[0] >= cl[0]):
                before = [(t,p) for t,p in references
                          if start-tolerance <= t < start]
                op = max(before,default=None,key=lambda x:x[0])
            # Minute archives often record at :40 instead of :49.
            # If the strict 15s edges fail, accept a real consecutive
            # near-boundary sample pair only with 45-75s elapsed; still
            # approximate, not a complete exchange candle. Never use a
            # price after the completed interval.
            if label == "1m" and not (op and cl and op[0] < cl[0]):
                closes = [(t,p) for t,p in references if stop-30 <= t < stop]
                opens = [(t,p) for t,p in references if start-30 <= t < start]
                pairs = [(a,b) for a in opens for b in closes
                         if 45 <= b[0]-a[0] <= 75]
                if pairs:
                    op,cl=min(pairs,key=lambda ab: (abs(ab[1][0]-ab[0][0]-60),
                                                     -ab[1][0],-ab[0][0]))
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
            # Short candle volume denomination remains unverified.
            "volume":_decimal(candidate.get("volume_24h_idr")) if daily else None,
        })
    return rows


def apply_ws3_trade_samples(candidate, rows, *, db_path, now_epoch):
    """Real executed IDR amounts; sample coverage is never full volume."""
    from .m05_radar_momentum_nonlive import _owned_private_regular
    pair,end=candidate["pair"],candidate["observed_epoch"]
    if not _owned_private_regular(Path(db_path)) or type(end) is not int or end>now_epoch:
        return rows
    start=min(r["start_epoch"] for r in rows if r["tf"]!="24h")
    raw=_read_rows(db_path, """SELECT exchange_epoch,sequence,price_idr,quote_idr
        FROM m03_ws3_trade_samples WHERE pair=? AND exchange_epoch>=?
        AND exchange_epoch<? AND recorded_epoch<=?
        ORDER BY exchange_epoch,sequence LIMIT 20001""",(pair,start,end,now_epoch))
    if not raw or len(raw)>20000:
        return rows
    trades=[]
    for stamp,seq,price,quote in raw:
        p,q=_decimal(price,positive=True),_decimal(quote,positive=True)
        if type(stamp) is not int or type(seq) is not int or p is None or q is None:
            return rows
        trades.append((stamp,seq,p,q))
    out=[]
    for row in rows:
        r=dict(row)
        if r["tf"]!="24h":
            sample=[t for t in trades if r["start_epoch"]<=t[0]<r["end_epoch"]]
            if sample:
                r["volume"]=sum((t[3] for t in sample),Decimal(0))
                r["volume_sampled"]=True
                if r["close_price"] is None:
                    r["close_price"]=sample[-1][2]
                    r["approx"]=True
        out.append(r)
    return out


def count_green(rows):
    """Count only eight distinct finite, strictly positive displayed returns."""
    if [row.get("tf") for row in rows] != [label for label,_ in FRAMES]:
        return 0
    return sum(isinstance(row.get("change"),Decimal)
               and row["change"].is_finite() and row["change"]>0
               for row in rows)


def eligible_premium_watch(candidate, rows):
    volume = _decimal(candidate.get("volume_24h_idr"))
    return volume is not None and volume>=MIN_TURNOVER_24H and count_green(rows)>=MIN_GREEN


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
                           candles=None, previous_24h=None, rows=None):
    """Mobile Telegram HTML, aligned inline text, no code-block chrome.

    Watch score and volume acceleration remain unavailable until real
    transaction-window evidence and a validated score definition exist.
    """
    if rows is None:
        rows = build_timeframes(candidate,references,candles=candles,
                                previous_24h=previous_24h)
    pair = candidate["pair"].upper().replace("_IDR", "/IDR")
    current = _number(_decimal(candidate["latest_price_idr"], positive=True))
    time = datetime.fromtimestamp(candidate["observed_epoch"], WIB).strftime("%H:%M:%S")
    # Telegram has no HTML tables. Individual inline code entities retain
    # fixed-width character cells without the preformatted Copy Code panel.
    # Put status emoji AFTER the cells so variable emoji fonts cannot shift
    # the numeric column starts. Never round/truncate exact close prices to fit.
    cells = []
    for row in rows:
        change = _percent(row["change"]) if row["change"] is not None else "-"
        close = _number(row["close_price"]) if row["close_price"] is not None else "-"
        volume = (_volume(row["volume"]).replace(" miliar", "M")
                  if row["volume"] is not None else "-")
        if row.get("volume_sampled") and row["volume"] is not None:
            volume = "~"+volume
        label = row["tf"] + ("~" if row["approx"] else "")
        cells.append((label, change, close, volume, _direction(row["change"])))
    headers = ("TF", "CHG", "CLOSE", "VOL")
    widths = tuple(max(len(h), *(len(r[i]) for r in cells))
                   for i,h in enumerate(headers))
    def aligned(values):
        return " ".join((values[0].ljust(widths[0]), *(
            values[i].rjust(widths[i]) for i in range(1,4))))
    if sum(widths)+3 <= 30:
        table = ["<code>"+escape(aligned(headers))+"</code>"]
        table += ["<code>"+escape(aligned(r[:4]))+"</code> "+r[4]
                  for r in cells]
    else:
        # Exceptional very long returns/prices: labeled two-line fallback
        # instead of wrapping an ambiguous table or sacrificing precision.
        table = ["<b>TF / CHANGE / CLOSE / VOL</b>"]
        for label, change, close, volume, direction in cells:
            table.append(f"{direction} <b>{escape(label)} · {escape(change)}</b>")
            table.append(escape(f"Close {close} · Vol {volume}"))
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
    ref = candidate.get("reference_epoch")
    trigger_raw = candidate.get("observed_rise_pct")
    independent = candidate.get("watch_path") == "INDEPENDENT_FOUR_GREEN"
    if type(ref) is int and 0 < ref < candidate["observed_epoch"] and trigger_raw is not None:
        trigger = Decimal(trigger_raw)
        elapsed = candidate["observed_epoch"] - ref
        heading = "Short move" if independent else "Trigger"
        context = f"{heading} {_percent(trigger)} / {elapsed//60}m{elapsed%60:02d}s"
    else:
        context = "Short move unavailable"
    green = count_green(rows)
    rolling = _decimal(candidate.get("volume_24h_idr"))
    total = ("Rp" + _number(rolling)) if rolling is not None else "—"
    daily = _percent(rows[-1]["change"]) if rows[-1]["change"] is not None else "—"
    known = sum(r["change"] is not None for r in rows)
    bar = "🟩"*green+"⬜"*(8-green)
    sample_text = None
    micro = candidate.get("microstructure_observation")
    if (source_kind == "WS3" and isinstance(micro, dict)
            and micro.get("status") == "PARTIAL_SOURCE_OBSERVATION"
            and micro.get("source_malformed") is False
            and micro.get("trade_activity_observed") is True
            and type(micro.get("source_capture_epoch")) is int
            and 0 <= now_epoch-micro["source_capture_epoch"] <= 90):
        buy = _decimal(micro.get("source_reported_buy_idr"))
        sell = _decimal(micro.get("source_reported_sell_idr"))
        if buy is not None and sell is not None:
            sample_text = "Trade sample · Rp" + _number(buy+sell) + " (partial)"
    lines = [
        f"📡 <b>PITIDEN RADAR</b> · {escape(time)} WIB",
        f"<b>{escape(pair)} · Rp{escape(current)}</b>",
        f"24h Change · <b>{escape(daily)}</b>",
        f"<b>24h Volume</b> · {escape(total)}",
        "",
        *table,
        "",
        f"{bar} <b>Green {green}/8</b>",
        escape(f"Momentum {_momentum(rows)} · Spread {spread_text}"),
        *(["<b>4/8 WATCH · independent scan</b>"] if independent else []),
        escape(context),
        *([escape(sample_text)] if sample_text is not None else []),
        f"<i>{known}/8 data · ~ sampled · - unavailable</i>",
        *(["<i>~VOL = observed trades only</i>"] if any(r.get("volume_sampled") for r in rows) else []),
        "<i>IDR · M=miliar · jt=juta · rb=ribu · T=triliun</i>",
        "<i>24h rolling · WATCH · Not BUY · AI/G1 unverified</i>",
    ]
    return "\n".join(lines)
