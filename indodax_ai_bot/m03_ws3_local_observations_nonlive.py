"""Bounded observation history from the original WS3 socket only.

Summary ticks are price samples. Trade OHLC describes received trades only.
Neither is a certified closed candle, total interval volume, or G1 evidence.
No new request, subscription, database, service or execution authority.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3

from .m03_ws3_evidence_archive_nonlive import (
    DEFAULT_EVIDENCE_DB, _prepare_private_target, _private_owned_regular,
    PAIR, RETENTION_SECONDS,
)
from .scanner import MAIN_EXCLUDED_PAIRS

MAX_SUMMARY = 6000
MAX_TRADES = 2000
MAX_ROWS = 250000
MIN_TURNOVER = Decimal("400000000")


def _number(value, *, positive=True):
    if type(value) not in (str, int, Decimal) or len(str(value)) > 90:
        return None
    try:
        n = Decimal(value)
        if not n.is_finite() or abs(n.adjusted()) > 20 or (
                n <= 0 if positive else n < 0):
            return None
        return format(n.normalize(), "f")
    except (InvalidOperation, ValueError):
        return None


def _pair(pair):
    return (type(pair) is str and PAIR.fullmatch(pair) is not None
            and pair not in MAIN_EXCLUDED_PAIRS)


class LocalObservationCapture:
    """Retain actual already-parsed observations; cap memory explicitly."""
    def __init__(self):
        self.summary = {}
        self.trades = {}
        self.summary_truncated = False
        self.trades_truncated = False

    def observe_summary(self, samples, offset, received):
        for row in samples:
            pair, stamp = row["pair"], row["observed_exchange_epoch"]
            price = _number(row["last_idr"])
            volume = _number(row["volume_24h_idr"], positive=False)
            if (not _pair(pair) or price is None or volume is None
                    or Decimal(volume) < MIN_TURNOVER
                    or type(stamp) is not int or not 0 <= received-stamp <= 90
                    or type(offset) is not int or offset < 0):
                continue
            key = (pair, stamp, price)
            if key in self.summary:
                continue
            if len(self.summary) >= MAX_SUMMARY:
                self.summary_truncated = True
                break
            self.summary[key] = (pair, stamp, price, received)

    def observe_trades(self, pair, trades, received):
        if not _pair(pair):
            return
        for row in trades:
            stamp, seq = row["exchange_epoch"], row["sequence"]
            price = _number(row["price_idr"])
            amount = _number(row["source_reported_idr_amount"])
            if (type(stamp) is not int or not 0 <= received-stamp <= 90
                    or type(seq) is not int or not 0 <= seq < 2**63
                    or price is None or amount is None):
                continue
            key = (pair, stamp, seq, price, amount)
            if key in self.trades:
                continue
            if len(self.trades) >= MAX_TRADES:
                self.trades_truncated = True
                break
            self.trades[key] = (*key, received)

    def snapshot(self, micro):
        # Drop trade observations for an explicitly malformed/gap/truncated
        # pair in this socket. Clean-looking observations are STILL partial.
        ok = {p for p,s in micro.items()
              if not any(s.get(k) is not False for k in (
                  "trade_gap_observed","trade_truncated","source_malformed"))}
        return {"summary":list(self.summary.values()),
                "trades":[r for r in self.trades.values() if r[0] in ok],
                "summary_truncated":self.summary_truncated,
                "trades_truncated":self.trades_truncated}


def archive_local_observations(source, *, now_epoch,
                               evidence_db=DEFAULT_EVIDENCE_DB):
    data = source["local_observations"]
    if (source.get("status") != "M03_WS3_PUBLIC_SUMMARY_OBSERVED"
            or source.get("public_ws_authentication_observed") is not True
            or type(now_epoch) is not int or now_epoch <= 0
            or not isinstance(data,dict)
            or not isinstance(data["summary"],list) or len(data["summary"])>MAX_SUMMARY
            or not isinstance(data["trades"],list) or len(data["trades"])>MAX_TRADES):
        raise ValueError("WS3_LOCAL_OBSERVATIONS_INVALID")
    summary, trades = [], []
    for row in data["summary"]:
        pair,stamp,price,received = row
        p = _number(price)
        if (_pair(pair) and p is not None and type(stamp) is int
                and type(received) is int and 0<=received-stamp<=90
                and 0<=now_epoch-received<=120):
            summary.append((pair,stamp,p,received,now_epoch))
    for row in data["trades"]:
        pair,stamp,seq,price,amount,received = row
        p,a = _number(price),_number(amount)
        if (_pair(pair) and p is not None and a is not None
                and type(stamp) is int and type(seq) is int and 0<=seq<2**63
                and type(received) is int and 0<=received-stamp<=90
                and 0<=now_epoch-received<=120):
            trades.append((pair,stamp,seq,p,a,received,now_epoch))
    path=Path(evidence_db)
    _prepare_private_target(path)
    with sqlite3.connect(path,timeout=2) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS ws3_local_summary_samples(
            pair TEXT NOT NULL, exchange_epoch INTEGER NOT NULL,
            price_idr TEXT NOT NULL, received_epoch INTEGER NOT NULL,
            archived_epoch INTEGER NOT NULL,
            PRIMARY KEY(pair,exchange_epoch,price_idr))""")
        db.execute("""CREATE TABLE IF NOT EXISTS ws3_local_trade_samples(
            pair TEXT NOT NULL, exchange_epoch INTEGER NOT NULL,
            sequence INTEGER NOT NULL, price_idr TEXT NOT NULL,
            amount_idr TEXT NOT NULL, received_epoch INTEGER NOT NULL,
            archived_epoch INTEGER NOT NULL,
            PRIMARY KEY(pair,exchange_epoch,sequence,price_idr,amount_idr))""")
        db.execute("CREATE INDEX IF NOT EXISTS ws3_local_summary_time "
                   "ON ws3_local_summary_samples(exchange_epoch)")
        db.execute("CREATE INDEX IF NOT EXISTS ws3_local_trade_time "
                   "ON ws3_local_trade_samples(exchange_epoch)")
        n1=db.executemany("INSERT OR IGNORE INTO ws3_local_summary_samples "
                          "VALUES(?,?,?,?,?)",summary).rowcount
        n2=db.executemany("INSERT OR IGNORE INTO ws3_local_trade_samples "
                          "VALUES(?,?,?,?,?,?,?)",trades).rowcount
        for table in ("ws3_local_summary_samples","ws3_local_trade_samples"):
            db.execute("DELETE FROM "+table+" WHERE exchange_epoch<?",
                       (now_epoch-RETENTION_SECONDS,))
            db.execute("DELETE FROM "+table+" WHERE rowid IN (SELECT rowid FROM "
                       +table+" ORDER BY exchange_epoch,rowid LIMIT "
                       "(SELECT MAX(COUNT(*)-?,0) FROM "+table+"))",(MAX_ROWS,))
    return {"status":"WS3_LOCAL_OBSERVATIONS_ARCHIVED_UNATTESTED",
            "summary_added":n1,"trades_added":n2,
            "summary_truncated":data.get("summary_truncated") is True,
            "trades_truncated":data.get("trades_truncated") is True,
            "extra_exchange_requests":0,"complete_trade_tape_verified":False,
            "execution_allowed":False}


def read_observed_minute_trade_ohlc(path, pair, start, *, now_epoch):
    """Partial OHLC and observed turnover only, not a full-minute total."""
    if (not _pair(pair) or type(start) is not int or start%60 or start<=0
            or type(now_epoch) is not int or start+60>now_epoch
            or not _private_owned_regular(Path(path))):
        return None
    try:
        with sqlite3.connect(Path(path).absolute().as_uri()+"?mode=ro",
                             uri=True,timeout=.15) as db:
            db.execute("PRAGMA query_only=ON")
            rows=db.execute(
                "SELECT exchange_epoch,sequence,price_idr,amount_idr "
                "FROM ws3_local_trade_samples WHERE pair=? AND exchange_epoch>=? "
                "AND exchange_epoch<? AND archived_epoch<=? "
                "AND received_epoch BETWEEN exchange_epoch AND exchange_epoch+90 "
                "AND received_epoch<=archived_epoch ORDER BY exchange_epoch,sequence "
                "LIMIT 2001",(pair,start,start+60,now_epoch)).fetchall()
    except (OSError,ValueError,sqlite3.Error):
        return None
    if not rows or len(rows)>2000:
        return None
    seen=set()
    prices=[]
    amounts=[]
    for stamp,seq,p,a in rows:
        if seq in seen or _number(p) is None or _number(a) is None:
            return None
        seen.add(seq)
        prices.append(Decimal(p))
        amounts.append(Decimal(a))
    return {"open":str(prices[0]),"high":str(max(prices)),"low":str(min(prices)),
            "close":str(prices[-1]),"observed_turnover_idr":str(sum(amounts)),
            "observed_trade_count":len(rows),"first_trade_epoch":rows[0][0],
            "last_trade_epoch":rows[-1][0],"complete":False,
            "source":"WS3_PARTIAL_TRADE_OBSERVATION","g1_verified":False}
