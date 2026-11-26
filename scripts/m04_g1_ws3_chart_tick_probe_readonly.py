"""One bounded WS3 chart-tick research probe against ORIGINAL raw trade rows.

No REST, retry, reconnect, scanner, service control, source DB writes,
private API or AI. A distinct public channel/connection is corroboration,
not authoritative exhaustive matching-engine history or G1 issuance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from indodax_ai_bot.trade_stream import WS_URL, PUBLIC_TOKEN, _trade_rows
from indodax_ai_bot.scanner import MAIN_EXCLUDED_PAIRS
from indodax_ai_bot.m05_radar_momentum_nonlive import DEFAULT_DB
from scripts.m04_g1_15m_1h_source_inventory_readonly import _optional_existing_journal_ok

_PAIR = re.compile(r"[a-z0-9]+_idr\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_LOCKS = {"INDODAX_TRADING_ENABLED":"NO",
          "INDODAX_ALLOW_LIVE_EXECUTION":"NO",
          "INDODAX_EXECUTION_MODE":"DRY_RUN"}
_MAX_FRAME=512000
_MAX_BYTES=8*1024*1024
_MAX_FRAMES=4096
_MAX_TICKS=10000
_MAX_LOCAL_PUBLICATIONS=20000
_MAX_LOCAL_BYTES=32*1024*1024
_MAX_LOCAL_PAYLOAD=131072


def _result(status, **fields):
    return {
        "stage":"M04_G1_WS3_CHART_TICK_FEASIBILITY_RESEARCH_ONLY",
        "status":status,
        "rest_requests":0,
        "independent_g1_verified":False,
        "independent_gap_recovery_verified":False,
        "complete_trade_tape_verified":False,
        "chart_volume_unit_equivalence_verified":False,
        "chart_trade_sequence_namespace_verified":False,
        "separate_channel_is_not_independent_exchange_attestation":True,
        "execution_allowed":False,"orders_sent":0,"provider_calls":0,
        "telegram_messages_sent":0,
        **fields,
    }


def _valid_pair(pair):
    return (type(pair) is str and len(pair)<=40 and _PAIR.fullmatch(pair)
            and pair not in MAIN_EXCLUDED_PAIRS)


def _positive(value):
    if type(value) not in (int,str,Decimal) or not 1<=len(str(value))<=72:
        raise ValueError("TICK_AMOUNT_FORMAT")
    value=Decimal(value)
    if not value.is_finite() or value<=0 or abs(value.adjusted())>30:
        raise ValueError("TICK_AMOUNT_RANGE")
    return value


def _tick_rows(rows, received):
    if type(rows) is not list or not 1<=len(rows)<=500:
        raise ValueError("CHART_TICK_ROWS_INVALID")
    seen=set()
    out=[]
    for row in rows:
        if (type(row) is not list or len(row)!=4
                or type(row[0]) is not int or not 0<=received-row[0]<=86400
                or type(row[1]) is not int or not 0<row[1]<2**64
                or (row[0],row[1]) in seen):
            raise ValueError("CHART_TICK_IDENTITY_INVALID")
        seen.add((row[0],row[1]))
        out.append((row[0],row[1],_positive(row[2]),_positive(row[3])))
    return out


def _messages(raw):
    # Support bounded concatenated public JSON frames without assuming
    # every websocket message contains exactly one JSON object.
    if type(raw) is not str or not raw or len(raw.encode("utf-8"))>_MAX_FRAME:
        raise ValueError("CHART_FRAME_INVALID")
    decoder=json.JSONDecoder(parse_float=Decimal)
    pos=0
    messages=[]
    while pos<len(raw):
        while pos<len(raw) and raw[pos].isspace():
            pos+=1
        if pos==len(raw):
            break
        obj,pos=decoder.raw_decode(raw,pos)
        if type(obj) is not dict or len(messages)>=64:
            raise ValueError("CHART_FRAME_ENVELOPE_INVALID")
        messages.append(obj)
    if not messages:
        raise ValueError("CHART_FRAME_EMPTY")
    return messages


def collect_chart_ticks(*, pair, seconds, ws_module, monotonic, epoch):
    """Single connection; fail closed on rejected ACK, gap or malformed data.

    Fresh subscription only, not a historical/replay probe. Returns internal
    parsed rows to caller, never prints socket frames or public token.
    """
    if not _valid_pair(pair) or type(seconds) is not int or not 30<=seconds<=120:
        raise ValueError("CHART_SCOPE_INVALID")
    channel="chart:tick-"+pair.replace("_","")
    sock=None
    ticks={}
    try:
        sock=ws_module.create_connection(WS_URL,timeout=5,enable_multithread=True)
        sock.settimeout(1)
        sock.send(json.dumps({"params":{"token":PUBLIC_TOKEN},"id":1}))
        phase="AUTH"
        deadline=monotonic()+8
        ack_epoch=baseline=last=None
        publication_count=frames=total_bytes=0
        next_ping=None
        ping_id=3
        while monotonic()<deadline:
            if phase=="OBSERVE" and monotonic()>=next_ping:
                sock.send(json.dumps({"method":7,"id":ping_id}))
                ping_id+=1
                next_ping=monotonic()+30
            try:
                raw=sock.recv()
            except ws_module.WebSocketTimeoutException:
                continue
            frames+=1
            if frames>_MAX_FRAMES:
                raise ValueError("CHART_FRAME_CAP")
            if type(raw) is not str:
                raise ValueError("CHART_FRAME_INVALID")
            total_bytes+=len(raw.encode("utf-8"))
            if total_bytes>_MAX_BYTES:
                raise ValueError("CHART_BYTE_CAP")
            for msg in _messages(raw):
                if monotonic()>=deadline:
                    break
                if "error" in msg or "disconnect" in msg:
                    raise ValueError("CHART_SERVER_REJECTED_OR_DISCONNECTED")
                result=msg.get("result")
                if phase=="AUTH":
                    if (type(msg.get("id")) is not int or msg["id"]!=1
                            or type(result) is not dict
                            or type(result.get("client")) is not str
                            or not result["client"]):
                        raise ValueError("CHART_AUTH_INVALID")
                    sock.send(json.dumps({"method":1,"params":{"channel":channel},"id":2}))
                    phase="SUBSCRIBE"
                    deadline=monotonic()+8
                elif phase=="SUBSCRIBE":
                    if (type(msg.get("id")) is not int or msg["id"]!=2
                            or type(result) is not dict
                            or result.get("recoverable") is not True
                            or type(result.get("epoch")) is not str
                            or not re.fullmatch(r"[0-9]{1,32}",result["epoch"])
                            or type(result.get("offset")) is not int
                            or not 0<=result["offset"]<2**63
                            or result.get("publications",[])!=[]):
                        raise ValueError("CHART_SUBSCRIBE_INVALID")
                    baseline=last=result["offset"]
                    ack_epoch=int(epoch())
                    phase="OBSERVE"
                    deadline=monotonic()+seconds
                    next_ping=monotonic()+30
                else:
                    if msg=={} or (type(msg.get("id")) is int
                                   and 3<=msg["id"]<ping_id
                                   and result in (None,{})):
                        continue
                    if (type(result) is not dict or result.get("channel")!=channel
                            or type(result.get("data")) is not dict):
                        raise ValueError("CHART_PUBLICATION_CHANNEL_INVALID")
                    body=result["data"]
                    off=body.get("offset")
                    if type(off) is not int or off!=last+1 or off>=2**63:
                        raise ValueError("CHART_PUBLICATION_OFFSET_GAP_OR_REPLAY")
                    received=int(epoch())
                    for tick in _tick_rows(body.get("data"),received):
                        key=tick[:2]
                        if key in ticks:
                            raise ValueError("CHART_REPEAT_OR_CONFLICTING_IDENTITY")
                        ticks[key]=tick
                        if len(ticks)>_MAX_TICKS:
                            raise ValueError("CHART_TICK_CAP")
                    publication_count+=1
                    last=off
        if phase!="OBSERVE":
            raise ValueError("CHART_HANDSHAKE_TIMEOUT")
        finish=int(epoch())
        if not ack_epoch<=finish<=ack_epoch+seconds+5:
            raise ValueError("CHART_CLOCK_INVALID")
        return dict(ack_epoch=ack_epoch,finish_epoch=finish,
                    baseline_offset=baseline,last_offset=last,
                    publication_count=publication_count,ticks=list(ticks.values()))
    finally:
        if sock is not None:
            try:
                sock.close(timeout=1)
            except Exception:
                pass


def _open_source(path):
    path=Path(path).absolute()
    if not _optional_existing_journal_ok(path):
        raise ValueError("ORIGINAL_RAW_SOURCE_INVALID")
    db=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=3)
    try:
        db.execute("PRAGMA query_only=ON")
        tables={r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"ws_trade_publications","m03_ws3_trade_publication_times"}<=tables:
            raise ValueError("ORIGINAL_RAW_SOURCE_TABLES_UNAVAILABLE")
        return db
    except BaseException:
        db.close()
        raise


def read_raw_overlap(path,pair,start,end,cutoff):
    """Read retained digest-validated raw rows, not the quantity-less index."""
    db=_open_source(path)
    try:
        db.execute("BEGIN")
        cursor=db.execute("""
            SELECT p.epoch,p.offset,p.payload_sha256,
                   CASE WHEN length(CAST(p.rows_json AS BLOB))<=?
                        THEN p.rows_json END,t.recorded_epoch
            FROM ws_trade_publications p
            LEFT JOIN m03_ws3_trade_publication_times t
              ON p.pair=t.pair AND p.epoch=t.epoch AND p.offset=t.offset
            WHERE p.pair=? ORDER BY p.epoch,p.offset LIMIT ?
        """,(_MAX_LOCAL_PAYLOAD,pair,_MAX_LOCAL_PUBLICATIONS+1))
        raw={}
        total=0
        for n,(stream_epoch,off,digest,payload,receipt) in enumerate(cursor,1):
            if (n>_MAX_LOCAL_PUBLICATIONS or type(stream_epoch) is not str
                    or not re.fullmatch(r"[0-9]{1,32}",stream_epoch)
                    or type(off) is not int or off<=0
                    or type(digest) is not str or not _HEX.fullmatch(digest)
                    or type(payload) is not str):
                raise ValueError("ORIGINAL_RAW_PUBLICATION_INVALID_OR_CAP")
            encoded=payload.encode("utf-8")
            total+=len(encoded)
            if total>_MAX_LOCAL_BYTES or sha256(encoded).hexdigest()!=digest:
                raise ValueError("ORIGINAL_RAW_DIGEST_OR_BYTE_CAP")
            for row in _trade_rows(json.loads(payload),pair):
                stamp,seq=row[1:3]
                if not start<=stamp<end:
                    continue
                identity=(stamp,seq)
                values=(row[3],_positive(row[4]),_positive(row[5]),_positive(row[6]))
                valid=type(receipt) is int and 0<=receipt-stamp<=86400
                observed=receipt if valid else None
                prior=raw.get(identity)
                if prior is not None:
                    if prior[0]!=values:
                        raise ValueError("ORIGINAL_RAW_IDENTITY_CONFLICT")
                    observed=min(x for x in (prior[1],observed) if x is not None) if (
                        prior[1] is not None or observed is not None) else None
                raw[identity]=(values,observed)
                if len(raw)>_MAX_TICKS:
                    raise ValueError("ORIGINAL_RAW_IDENTITY_CAP")
        rows=[(key[0],key[1],value[1],value[2],value[3])
              for key,(value,receipt) in raw.items()
              if receipt is not None and receipt<=cutoff]
        return rows,sum(receipt is None for _,receipt in raw.values()),sum(
            receipt is not None and receipt>cutoff for _,receipt in raw.values())
    finally:
        db.close()


def audit_ack_only_local_activity(*, source_db, pair, ack_epoch, finish_epoch,
                                  now_epoch=None):
    """Read original raw activity within a reported ACK-only probe interval.

    The interval is an operator-reported local connection window, not a
    chart-exchange-time coverage attestation. Exclude the ACK second and
    the unfinished final second. Do not treat empty local data as proof of
    exchange inactivity, or locally observed trade data as proof of a chart
    tick outage. No transport, writes or service action.
    """
    now=int(time.time()) if now_epoch is None else now_epoch
    base=dict(local_ack_window_is_not_chart_coverage=True,
              local_empty_is_not_exchange_inactivity=True,
              local_nonempty_does_not_prove_chart_outage=True,
              local_raw_activity_status="LOCAL_ACK_WINDOW_SCOPE_INVALID")
    if (not _valid_pair(pair) or any(type(t) is not int for t in
                (ack_epoch,finish_epoch,now))
            or not 1500000000<=ack_epoch<finish_epoch<=now
            or not 2<=finish_epoch-ack_epoch<=125
            or now-ack_epoch>23*3600):
        return base
    start=ack_epoch+1
    end=finish_epoch
    base.update(local_window_start_epoch=start,local_window_end_epoch=end,
                local_receipt_cutoff_epoch=finish_epoch)
    try:
        rows,unknown,late=read_raw_overlap(
            source_db,pair,start,end,finish_epoch)
    except (OSError,ValueError,TypeError,InvalidOperation,sqlite3.Error,OverflowError):
        return dict(base,local_raw_activity_status="LOCAL_ACK_WINDOW_SOURCE_UNAVAILABLE")
    return dict(base,
                local_raw_activity_status=(
                    "LOCAL_TRADE_ACTIVITY_OBSERVED" if rows else
                    "NO_ON_TIME_LOCAL_TRADE_OBSERVED_UNVERIFIED"),
                local_raw_trade_rows_by_finish=len(rows),
                local_raw_trade_seconds_by_finish=len({r[0] for r in rows}),
                local_raw_unknown_receipts=unknown,
                local_raw_late_receipts=late)


def compare_overlap(capture, local_rows, *, start, end):
    chart=[r for r in capture["ticks"] if start<=r[0]<end]
    local=[r for r in local_rows if start<=r[0]<end]
    c=Counter((t,p,v) for t,_,p,v in chart)
    base=Counter((t,p,b) for t,_,p,_,b in local)
    quote=Counter((t,p,q) for t,_,p,q,_ in local)
    ids_c={(t,seq):(p,v) for t,seq,p,v in chart}
    ids_l={(t,seq):(p,q,b) for t,seq,p,q,b in local}
    both=ids_c.keys() & ids_l.keys()
    return dict(
        comparable_seconds=max(0,end-start),
        chart_comparable_rows=len(chart),local_comparable_rows=len(local),
        time_price_volume_matches_if_chart_is_base=sum((c&base).values()),
        chart_only_if_base=sum((c-base).values()),
        local_only_if_base=sum((base-c).values()),
        time_price_volume_matches_if_chart_is_quote=sum((c&quote).values()),
        shared_timestamp_sequence_identities=len(both),
        shared_identity_price_base_matches=sum(
            ids_c[k]==(ids_l[k][0],ids_l[k][2]) for k in both),
        shared_identity_price_quote_matches=sum(
            ids_c[k]==(ids_l[k][0],ids_l[k][1]) for k in both),
        nonempty_base_signature_agreement=bool(c) and c==base,
        nonempty_quote_signature_agreement=bool(c) and c==quote,
        comparison_is_retrospective_local_snapshot_not_atomic=True,
    )


def run(*, pair, seconds, source_db=DEFAULT_DB, ws_module=None,
        monotonic=None, epoch=None):
    if not _valid_pair(pair) or type(seconds) is not int or not 30<=seconds<=120:
        return _result("CHART_SCOPE_INVALID",connection_attempts=0)
    if any(os.environ.get(k)!=v for k,v in _LOCKS.items()):
        return _result("CHART_EXPLICIT_NONLIVE_LOCKS_REQUIRED",connection_attempts=0)
    try:
        _open_source(source_db).close()
    except (OSError,ValueError,sqlite3.Error):
        return _result("CHART_ORIGINAL_RAW_SOURCE_UNAVAILABLE",connection_attempts=0)
    if ws_module is None:
        try:
            import websocket as ws_module
        except ImportError:
            return _result("CHART_WS_CLIENT_MISSING",connection_attempts=0)
    try:
        cap=collect_chart_ticks(pair=pair,seconds=seconds,ws_module=ws_module,
                               monotonic=monotonic or time.monotonic,epoch=epoch or time.time)
    except (ValueError,InvalidOperation) as exc:
        reason=str(exc)
        return _result("CHART_PROBE_BLOCKED",connection_attempts=1,
                       reason=reason if re.fullmatch(r"CHART_[A-Z0-9_]{1,100}",reason)
                       else "CHART_FRAME_INVALID")
    except Exception:
        return _result("CHART_PROBE_TRANSPORT_UNAVAILABLE",connection_attempts=1)
    ticks=cap.pop("ticks")
    stats=dict(pair=pair,connection_attempts=1,chart_ticks_observed=len(ticks),**cap)
    if not ticks:
        context=audit_ack_only_local_activity(
            source_db=source_db,pair=pair,ack_epoch=cap["ack_epoch"],
            finish_epoch=cap["finish_epoch"],now_epoch=int((epoch or time.time)()))
        return _result("CHART_ACK_ONLY_NO_TICKS",**stats,**context)
    start=max(cap["ack_epoch"]+1,min(t[0] for t in ticks)+1)
    end=min(cap["finish_epoch"],max(t[0] for t in ticks))
    if end<=start:
        return _result("CHART_NO_INTERIOR_OVERLAP",**stats)
    try:
        rows,unknown,late=read_raw_overlap(source_db,pair,start,end,cap["finish_epoch"])
        comparison=compare_overlap({"ticks":ticks},rows,start=start,end=end)
    except (OSError,ValueError,TypeError,InvalidOperation,sqlite3.Error,OverflowError):
        return _result("CHART_LOCAL_COMPARISON_BLOCKED",**stats)
    return _result("CHART_TICK_RAW_COMPARISON_RESEARCH_ONLY",**stats,
                   overlap_start_epoch=start,overlap_end_epoch=end,
                   local_raw_unknown_receipts=unknown,local_raw_late_receipts=late,
                   comparison=comparison)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pair",required=True)
    p.add_argument("--seconds",type=int,default=120)
    p.add_argument("--observe-public-chart-tick",action="store_true",required=True)
    args=p.parse_args(argv)
    report=run(pair=args.pair,seconds=args.seconds)
    print(json.dumps(report,sort_keys=True,separators=(",",":")))
    return 0 if report["status"] in (
        "CHART_TICK_RAW_COMPARISON_RESEARCH_ONLY","CHART_ACK_ONLY_NO_TICKS",
        "CHART_NO_INTERIOR_OVERLAP") else 2


if __name__=="__main__":
    raise SystemExit(main())
