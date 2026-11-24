"""Compare one admitted public V2 recent-trades page to original WS index.

Research only. Compare multiplicities of (exchange second, price, IDR quote)
within the page's interior seconds, without assuming REST trade IDs equal WS
sequences. A page is bounded and retrospective; equal signatures are not
unique trade identity or completeness proof. Never bypass existing egress or
REST quota, retry, page, write a database, restart services, issue G1 or trade.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from indodax_ai_bot.public_market import (
    PublicDataError,
    summarize_v2_trade_page_research,
)
from indodax_ai_bot.scanner import MAIN_EXCLUDED_PAIRS
from indodax_ai_bot.m05_radar_momentum_nonlive import DEFAULT_DB
from scripts.m04_g1_15m_1h_source_inventory_readonly import (
    _optional_existing_journal_ok,
)

_PAIR = re.compile(r"[a-z0-9]+_idr\Z")
_MAX_ROWS = 100000
_LOCKS = {
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
}


def _result(status, **fields):
    return {
        "stage": "M04_G1_V2_PAGE_WS_SIGNATURE_RESEARCH_ONLY",
        "status": status,
        "signature_match_is_not_trade_identity_proof": True,
        "rest_id_equals_ws_sequence_verified": False,
        "rest_page_exhaustiveness_verified": False,
        "amount_precision_equivalence_verified": False,
        "independent_gap_recovery_verified": False,
        "independent_g1_verified": False,
        "complete_trade_tape_verified": False,
        "execution_allowed": False,
        "orders_sent": 0,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        **fields,
    }


def _valid_pair(pair):
    return (type(pair) is str and len(pair)<=40 and
            _PAIR.fullmatch(pair) is not None and pair not in MAIN_EXCLUDED_PAIRS)


def _decimal(raw, *, zero=False):
    if type(raw) is not str or not 1<=len(raw)<=72:
        raise ValueError("AMOUNT_FORMAT")
    value=Decimal(raw)
    if (not value.is_finite() or value<0 or (not zero and value==0)
            or abs(value.adjusted())>30):
        raise ValueError("AMOUNT_RANGE")
    return value


def _local_rows(path, pair, end):
    path=Path(path).absolute()
    if not _optional_existing_journal_ok(path):
        raise ValueError("LOCAL_SOURCE_INVALID")
    db=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=3)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        rows=db.execute("""
            SELECT exchange_epoch,sequence,price_idr,quote_idr,recorded_epoch
            FROM m03_ws3_trade_samples
            WHERE pair=? AND exchange_epoch>=? AND exchange_epoch<?
            ORDER BY exchange_epoch,sequence LIMIT ?
        """,(pair,end-3600,end,_MAX_ROWS+1)).fetchall()
        if len(rows)>_MAX_ROWS:
            raise ValueError("LOCAL_ROW_CAP")
        return rows
    finally:
        db.close()


def _validated_local(rows, *, end, read_epoch):
    if type(rows) is not list or len(rows)>_MAX_ROWS:
        raise ValueError("LOCAL_ROW_CAP")
    seen=set()
    out=[]
    for stamp,seq,price,quote,receipt in rows:
        if (type(stamp) is not int or not end-3600<=stamp<end
                or type(seq) is not int or seq<=0
                or type(receipt) is not int or not stamp<=receipt<=read_epoch
                or (stamp,seq) in seen):
            raise ValueError("LOCAL_ROW_INVALID")
        seen.add((stamp,seq))
        out.append(((stamp,_decimal(price),_decimal(quote)),receipt))
    return out


def compare_page(*, pair, payload, rows, requested_epoch, received_epoch):
    if (not _valid_pair(pair) or type(requested_epoch) is not int
            or type(received_epoch) is not int
            or requested_epoch<1500003600
            or not 0<=received_epoch-requested_epoch<=30):
        raise ValueError("PAGE_SCOPE_INVALID")
    # Existing strict parser checks IDs, milliseconds, amounts and page limit.
    page=summarize_v2_trade_page_research(
        payload,requested_epoch=requested_epoch,received_epoch=received_epoch)
    end=requested_epoch//60*60
    local=_validated_local(rows,end=end,read_epoch=received_epoch)
    oldest=page["oldest_trade_ms"]
    newest=page["newest_trade_ms"]
    # Exclude both edge seconds: one page might cut through a second.
    interior_start=oldest//1000+1 if oldest is not None else None
    interior_end=newest//1000 if newest is not None else None
    signatures=[(item["time"]//1000,_decimal(item["price"]),
                 _decimal(item["quoteQty"],zero=True)) for item in payload]
    windows={}
    for label,seconds in (("15m",900),("1h",3600)):
        start=end-seconds
        a=max(start,interior_start) if interior_start is not None else end
        b=min(end,interior_end) if interior_end is not None else end
        inside=lambda t: a<=t<b
        ws_all=[sig for sig,receipt in local if start<=sig[0]<end and receipt<=end]
        ws=Counter(sig for sig in ws_all if inside(sig[0]))
        rest=Counter(sig for sig in signatures if inside(sig[0]))
        matched=ws & rest
        windows[label]={
            "window_start_epoch":start,
            "window_end_epoch":end,
            "page_interior_overlap_seconds":max(0,b-a),
            "page_interior_spans_full_window":a==start and b==end,
            "local_asof_rows_total":len(ws_all),
            "local_asof_rows_outside_comparable_seconds":len(ws_all)-sum(ws.values()),
            "local_late_rows":sum(start<=sig[0]<end and receipt>end for sig,receipt in local),
            "local_comparable_rows":sum(ws.values()),
            "rest_comparable_rows":sum(rest.values()),
            "matching_signature_multiplicity":sum(matched.values()),
            "ws_only_signature_multiplicity":sum((ws-rest).values()),
            "rest_only_signature_multiplicity":sum((rest-ws).values()),
            "nonempty_exact_signature_multiset_agreement":bool(ws) and ws==rest,
            "complete_trade_tape_verified":False,
        }
    return _result("V2_PAGE_WS_SIGNATURE_RESEARCH_ONLY",
                   pair=pair,window_end_epoch=end,
                   page_trade_count=page["page_trade_count"],
                   page_at_limit=page["page_at_limit"],
                   oldest_trade_ms=oldest,newest_trade_ms=newest,
                   windows=windows)


def run(*, pair, source_db=DEFAULT_DB, market_factory=None, clock=None):
    # Owner decision 24 Nov 2026: retire this G1 network probe. Preserve
    # explicitly injected offline fixtures and historical comparison code.
    # The normal CLI cannot make another REST request.
    if market_factory is None:
        return _result("V2_PAGE_G1_RETIRED_BY_OWNER",recent_trade_method_calls=0)
    if not _valid_pair(pair):
        return _result("PAIR_INVALID",recent_trade_method_calls=0)
    if any(os.environ.get(k)!=v for k,v in _LOCKS.items()):
        return _result("EXPLICIT_NONLIVE_LOCKS_REQUIRED",recent_trade_method_calls=0)
    now=clock or time.time
    try:
        # Snapshot before the single admitted request. Freeze closed cutoff
        # before the read; abort on minute rollover instead of requerying.
        start=int(now())
        end=start//60*60
        rows=_local_rows(source_db,pair,end)
        requested=int(now())
        if requested//60*60!=end or not 0<=requested-start<=10:
            raise ValueError("LOCAL_SNAPSHOT_CUTOFF_CHANGED")
        _validated_local(rows,end=end,read_epoch=requested)
        market=market_factory()
    except (OSError,ValueError,TypeError,OverflowError,sqlite3.Error,InvalidOperation):
        return _result("LOCAL_SOURCE_OR_ADMISSION_SETUP_BLOCKED",recent_trade_method_calls=0)
    try:
        # This is the existing admission + budget-gated method. A method
        # invocation is not proof an HTTP request was actually sent.
        payload=market.recent_trades_sample(pair)
        received=int(now())
        return dict(compare_page(pair=pair,payload=payload,rows=rows,
                    requested_epoch=requested,received_epoch=received),
                    recent_trade_method_calls=1)
    except PublicDataError as exc:
        reason=str(exc)
        # Only sanitized existing fixed uppercase reason codes.
        return _result("V2_PAGE_BLOCKED_OR_UNAVAILABLE",
                       reason=reason if re.fullmatch(r"[A-Z0-9_]{1,100}",reason)
                       else "PUBLIC_DATA_ERROR",
                       recent_trade_method_calls=1)
    except (OSError,ValueError,TypeError,OverflowError,InvalidOperation):
        return _result("V2_PAGE_INVALID_OR_UNAVAILABLE",recent_trade_method_calls=1)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pair",required=True)
    p.add_argument("--fetch-admitted-public-v2-page",action="store_true",required=True)
    args=p.parse_args(argv)
    report=run(pair=args.pair)
    print(json.dumps(report,sort_keys=True,separators=(",",":")))
    return 0 if report["status"]=="V2_PAGE_WS_SIGNATURE_RESEARCH_ONLY" else 2


if __name__=="__main__":
    raise SystemExit(main())
