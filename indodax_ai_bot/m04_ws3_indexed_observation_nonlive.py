"""As-of local WS3 trade observations for existing M04 role INPUT packets.

This is a bounded, read-only join of ORIGINAL M03 SQLite data. It cannot
prove the complete exchange trade tape, independent G1 source, a BUY edge,
ownership, paid AI results, or actual Telegram sender consumption.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import sqlite3
import stat

_PAIR = re.compile(r"[a-z0-9]+_idr\Z")
_WINDOWS = (("1m",60),("5m",300),("15m",900),("1h",3600))
_MAX_ROWS = 4000


def _positive(value: object, *, zero_ok: bool = False) -> Decimal:
    if type(value) not in (str,int,Decimal):
        raise ValueError("WS3_OBSERVED_NON_NUMERIC")
    if type(value) is str and len(value)>60:
        raise ValueError("WS3_OBSERVED_NUMERIC_LENGTH")
    try:
        d=Decimal(value)
    except (InvalidOperation,ValueError):
        raise ValueError("WS3_OBSERVED_INVALID_DECIMAL") from None
    if not d.is_finite() or abs(d)>=Decimal("1e25") or (d<0 if zero_ok else d<=0):
        raise ValueError("WS3_OBSERVED_NON_POSITIVE")
    return d


def _missing(status: str, observed_epoch: int) -> dict:
    return {
        "status":status,
        "candidate_observed_epoch":observed_epoch,
        "complete_trade_tape_verified":False,
        "independent_g1_issuer_verified":False,
        "trade_aggressor_verified":False,
        "trading_execution_allowed":False,
        "source_is_original_local_ws3_index":True,
    }


def inspect_ws3_indexed_trade_asof_nonlive(
        *, db_path: Path, candidate_epochs: dict[str,int],
        now_epoch: int) -> dict[str,dict] | None:
    """Only latest two M04 selected candidates, never all-market rescanning.

    Returns None if the ORIGINAL M03 indexed trade table is not installed.
    Missing evidence is UNKNOWN; no trade observed != no market trades.
    """
    if (type(now_epoch) is not int or now_epoch<=0
            or type(candidate_epochs) is not dict
            or len(candidate_epochs)>2
            or any(type(p) is not str or _PAIR.fullmatch(p) is None
                   or type(t) is not int or not 0<=now_epoch-t<=150
                   for p,t in candidate_epochs.items())):
        raise ValueError("M04_WS3_INDEXED_INVALID_SCOPE")
    p=Path(db_path)
    if p.is_symlink() or not p.is_file() or not stat.S_ISREG(p.stat().st_mode):
        return None
    if p.stat().st_mode & 0o077:
        return None
    output={}
    try:
        with sqlite3.connect(p.resolve().as_uri()+"?mode=ro",uri=True,
                             timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            has=db.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='m03_ws3_trade_samples'""").fetchone()
            if not has:
                return None
            has_price=db.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='m03_ws3_price_samples_5s'""").fetchone()
            has_state=db.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='ws_stream_state'""").fetchone()
            for pair,observed in candidate_epochs.items():
                try:
                    # Exchange and LOCAL receipt clocks BOTH precede source.
                    # Excludes later replay discoveries from an old alert.
                    rows=db.execute("""SELECT exchange_epoch,sequence,price_idr,
                        quote_idr,recorded_epoch FROM m03_ws3_trade_samples
                        WHERE pair=? AND exchange_epoch>=? AND exchange_epoch<=?
                        AND recorded_epoch<=? ORDER BY exchange_epoch DESC,
                        sequence DESC LIMIT ?""",
                        (pair,observed-3600,observed,observed,_MAX_ROWS+1)).fetchall()
                    capped=len(rows)>_MAX_ROWS
                    if capped:
                        rows=rows[:_MAX_ROWS]
                    trades=[]
                    for stamp,seq,price,quote,receipt in rows:
                        if (type(stamp) is not int or type(seq) is not int
                                or type(receipt) is not int or seq<0
                                or stamp>observed or receipt>observed
                                or stamp<observed-3600):
                            raise ValueError("M04_WS3_INDEXED_ROW_CLOCK")
                        trades.append((stamp,seq,_positive(price),_positive(quote,zero_ok=True)))
                    trades.sort()
                    closed_end=observed//60*60
                    windows={}
                    for label,duration in _WINDOWS:
                        start=closed_end-duration
                        items=[t for t in trades if start<=t[0]<closed_end]
                        windows[label]={
                            "window_start_epoch":start,
                            "window_end_epoch":closed_end,
                            "locally_observed_trade_rows":len(items),
                            "observed_quote_idr_only":str(sum((t[3] for t in items),Decimal(0))) if items else None,
                            "first_observed_trade_epoch":items[0][0] if items else None,
                            "last_observed_trade_epoch":items[-1][0] if items else None,
                            "observed_open_price_idr":str(items[0][2]) if items else None,
                            "observed_close_price_idr":str(items[-1][2]) if items else None,
                            "observed_high_price_idr":str(max(t[2] for t in items)) if items else None,
                            "observed_low_price_idr":str(min(t[2] for t in items)) if items else None,
                            "full_window_coverage_verified":False,
                        }
                    detail={
                        **_missing("OBSERVED_LOCAL_TRADE_SAMPLES_ONLY" if trades else
                                   "NO_LOCAL_TRADE_SAMPLES_AT_SOURCE_TIME",observed),
                        "source_asof_not_newer_than":observed,
                        "closed_windows":windows,
                        "local_trade_read_cap_hit":capped,
                        "unknown_missing_publications":True,
                        "trade_count_is_only_observed":True,
                    }
                    if has_price:
                        recent=db.execute("""SELECT exchange_epoch,last_idr,
                            recorded_epoch FROM m03_ws3_price_samples_5s
                            WHERE pair=? AND exchange_epoch<=? AND recorded_epoch<=?
                            ORDER BY exchange_epoch DESC LIMIT 1""",
                            (pair,observed,observed)).fetchone()
                        if recent:
                            stamp,price,receipt=recent
                            if (type(stamp) is not int or type(receipt) is not int
                                    or stamp>observed or receipt>observed):
                                raise ValueError("M04_WS3_PRICE_CLOCK")
                            detail["local_price_5s_asof"]={
                                "exchange_epoch":stamp,
                                "observed_age_seconds":observed-stamp,
                                "last_idr":str(_positive(price)),
                                "source_freshness_not_complete_tape":True,
                            }
                    if has_state:
                        state=db.execute("SELECT status FROM ws_stream_state WHERE pair=?",
                                         (pair,)).fetchone()
                        detail["journal_state_at_inspection_not_historical"] = (
                            state[0] if state else "NO_SUBSCRIPTION_BASELINE")
                    output[pair]=detail
                except (sqlite3.Error,ValueError,TypeError,ArithmeticError) as exc:
                    output[pair]=_missing("INDEXED_LOCAL_EVIDENCE_INVALID_OR_UNREADABLE",
                                          observed)
            return output
    except sqlite3.Error:
        return {pair:_missing("INDEXED_DB_READ_UNAVAILABLE",obs)
                for pair,obs in candidate_epochs.items()}
