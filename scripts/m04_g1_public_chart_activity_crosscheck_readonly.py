"""Cross-check local WS trade activity against public chart OHLCV, nonlive.

One explicitly owner-invoked public chart GET using the existing M09
reference transport. Different endpoint, not independently authenticated
trade tape. No trade ID, volume-unit equivalence, historical as-of candle
availability or exact completeness assumptions. No retry, REST trade
fallback, DB writes, service action, private API, AI or execution.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

from indodax_ai_bot.m05_radar_momentum_nonlive import DEFAULT_DB
from indodax_ai_bot.scanner import MAIN_EXCLUDED_PAIRS
from scripts.m04_g1_15m_1h_source_inventory_readonly import (
    _optional_existing_journal_ok,
)
from scripts.m09_indodax_public_candle_reference_readonly import (
    _parse_candles, _positive, _validate_pair,
    fetch_indodax_public_1m_candles,
)

_WINDOWS = (("15m", 900), ("1h", 3600))
_MAX_ROWS = 100000
_LOCKS = {
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
}


def _result(status, **fields):
    return {
        "stage": "M04_G1_PUBLIC_CHART_ACTIVITY_CROSSCHECK_RESEARCH_ONLY",
        "status": status,
        "independent_g1_verified": False,
        "complete_trade_tape_verified": False,
        "independent_gap_recovery_verified": False,
        "chart_is_not_trade_tape": True,
        "chart_volume_unit_equivalence_verified": False,
        "chart_asof_historical_cutoff_verified": False,
        "trade_index_completeness_verified": False,
        "execution_allowed": False,
        "orders_sent": 0,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        **fields,
    }


def _valid_scope(pair, end, now):
    try:
        _validate_pair(pair)
    except ValueError:
        return False
    return (len(pair) <= 40 and pair not in MAIN_EXCLUDED_PAIRS
            and type(end) is int and type(now) is int
            and end >= 1500003600 and end % 60 == 0
            and 0 <= now-end <= 23*3600)


def read_local_rows(source_db, pair, end):
    """Only original existing index, read-only snapshot, no schema creation."""
    path = Path(source_db).absolute()
    if not _optional_existing_journal_ok(path):
        raise ValueError("ORIGINAL_LOCAL_INDEX_UNAVAILABLE")
    db = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=3)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        rows = db.execute("""
            SELECT exchange_epoch,sequence,price_idr,recorded_epoch
            FROM m03_ws3_trade_samples
            WHERE pair=? AND exchange_epoch>=? AND exchange_epoch<?
            ORDER BY exchange_epoch,sequence LIMIT ?
        """, (pair,end-3600,end,_MAX_ROWS+1)).fetchall()
        if len(rows)>_MAX_ROWS:
            raise ValueError("LOCAL_INDEX_CAP")
        return rows
    finally:
        db.close()


def compare_activity(*, payload, rows, pair, end, now):
    """Compare minute activity and observed price envelope, not quantities.

    Missing chart bars are UNKNOWN. Positive chart volume with no local rows
    is a discrepancy to investigate, not proof of a missing individual trade.
    Local trade prices are only checked against the chart range, with no
    tolerance or synthetic interpolation. Later receipts are not as-of rows.
    """
    if not _valid_scope(pair,end,now):
        raise ValueError("CHART_SCOPE_INVALID")
    candles = _parse_candles(payload,from_epoch=end-3600,to_epoch=end)
    if type(rows) is not list or len(rows)>_MAX_ROWS:
        raise ValueError("LOCAL_INDEX_CAP")
    local = {}
    identities = set()
    for stamp, seq, price, received in rows:
        if (type(stamp) is not int or not end-3600 <= stamp < end
                or type(seq) is not int or seq<=0
                or type(received) is not int or received<stamp
                or received>now):
            raise ValueError("LOCAL_INDEX_ROW_INVALID")
        identity=(stamp,seq)
        if identity in identities:
            raise ValueError("LOCAL_INDEX_DUPLICATE")
        identities.add(identity)
        price = _positive(price)
        minute=stamp//60*60
        bucket=local.setdefault(minute,{"prices":[],"late":0})
        if received>end:
            bucket["late"]+=1
        else:
            bucket["prices"].append(price)
    bounds={entry["Time"]:(_positive(entry["Low"]),_positive(entry["High"]))
            for entry in payload}
    windows={}
    for label,seconds in _WINDOWS:
        result={
            "expected_minutes":seconds//60,
            "chart_minutes_available":0,
            "chart_minutes_missing_unknown":0,
            "positive_chart_volume_minutes":0,
            "positive_chart_volume_without_local_asof_trade_minutes":0,
            "zero_chart_volume_with_local_asof_trade_minutes":0,
            "local_asof_trade_minutes":0,
            "local_asof_trade_rows":0,
            "local_late_trade_rows":0,
            "local_prices_outside_chart_range_minutes":0,
            "positive_chart_volume_without_local_asof_trade_minute_epochs":[],
            "zero_chart_volume_with_local_asof_trade_minute_epochs":[],
            "local_prices_outside_chart_range_minute_epochs":[],
        }
        for minute in range(end-seconds,end,60):
            bucket=local.get(minute,{"prices":[],"late":0})
            prices=bucket["prices"]
            result["local_asof_trade_minutes"]+=bool(prices)
            result["local_asof_trade_rows"]+=len(prices)
            result["local_late_trade_rows"]+=bucket["late"]
            if minute not in candles:
                result["chart_minutes_missing_unknown"]+=1
                continue
            result["chart_minutes_available"]+=1
            _,volume=candles[minute]
            result["positive_chart_volume_minutes"]+=volume>0
            if volume>0 and not prices:
                key="positive_chart_volume_without_local_asof_trade"
                result[key+"_minutes"]+=1
                result[key+"_minute_epochs"].append(minute)
            if volume==0 and prices:
                key="zero_chart_volume_with_local_asof_trade"
                result[key+"_minutes"]+=1
                result[key+"_minute_epochs"].append(minute)
            low,high=bounds[minute]
            if prices and (min(prices)<low or max(prices)>high):
                key="local_prices_outside_chart_range"
                result[key+"_minutes"]+=1
                result[key+"_minute_epochs"].append(minute)
        windows[label]=result
    return _result("PUBLIC_CHART_LOCAL_ACTIVITY_RESEARCH_ONLY",
                   pair=pair,window_end_epoch=end,windows=windows,
                   chart_symbol=_validate_pair(pair),
                   chart_symbol_alias_independently_verified=False)


def run(*, source_db, pair, end, now, fetch=None):
    if not _valid_scope(pair,end,now):
        return _result("CHART_SCOPE_INVALID",public_chart_gets_attempted=0)
    if any(os.environ.get(k)!=v for k,v in _LOCKS.items()):
        return _result("CHART_EXPLICIT_NONLIVE_LOCKS_REQUIRED",public_chart_gets_attempted=0)
    try:
        rows=read_local_rows(source_db,pair,end)
        # Validate local rows before any public request, including empty rows.
        compare_activity(payload=[],rows=rows,pair=pair,end=end,now=now)
    except (OSError,ValueError,TypeError,sqlite3.Error,OverflowError):
        return _result("CHART_LOCAL_SOURCE_BLOCKED",public_chart_gets_attempted=0)
    try:
        payload=(fetch or fetch_indodax_public_1m_candles)(
            pair,from_epoch=end-3600,to_epoch=end)
        result=compare_activity(payload=payload,rows=rows,pair=pair,end=end,now=now)
        return dict(result,public_chart_gets_attempted=1)
    except (OSError,ValueError,TypeError,OverflowError):
        return _result("PUBLIC_CHART_UNAVAILABLE_OR_INVALID",public_chart_gets_attempted=1)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pair",required=True)
    p.add_argument("--end-wib",required=True)
    p.add_argument("--fetch-public-ohlcv",action="store_true",required=True)
    args=p.parse_args(argv)
    now=int(time.time())
    try:
        end=int(datetime.strptime(args.end_wib,"%Y-%m-%dT%H:%M:%S")
                .replace(tzinfo=ZoneInfo("Asia/Jakarta")).timestamp())
    except (ValueError,OverflowError):
        result=_result("CHART_SCOPE_INVALID",public_chart_gets_attempted=0)
    else:
        result=run(source_db=DEFAULT_DB,pair=args.pair,end=end,now=now)
    print(json.dumps(result,sort_keys=True,separators=(",",":")))
    return 0 if result["status"]=="PUBLIC_CHART_LOCAL_ACTIVITY_RESEARCH_ONLY" else 2


if __name__=="__main__":
    raise SystemExit(main())
