"""Choose a prospective chart validator pair using fresh original M03 evidence.

No connection, REST, new scanner, allocation change, G1 or order authority.
Two recent ACK samples and positive locally indexed trade activity are a
selection heuristic, not proof of continuous subscription or future ticks.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import re

from scripts.m04_g1_local_ack_windows_readonly import _journal_read, _decode_records
from scripts.m04_g1_ws3_chart_tick_probe_readonly import (
    DEFAULT_DB, _open_source, _valid_pair,
)

_MAX_ROWS=50000
_FAILURE=re.compile(
    r"\bM03_WS_WATCH_JSON_INVALID:(?:AUTH|SUBSCRIBE|OBSERVE|UNKNOWN):"
    r"(?:EMPTY_TEXT|NONJSON_MARKUP|STRUCTURED_EXTRA_DATA|STRUCTURED_INCOMPLETE|"
    r"STRUCTURED_PROPERTY|STRUCTURED_COLON|STRUCTURED_SEPARATOR|STRUCTURED_VALUE|"
    r"STRUCTURED_CONTROL|STRUCTURED_UNICODE|STRUCTURED_ESCAPE|STRUCTURED_INVALID|"
    r"PRIMITIVE_INVALID|NONJSON_TEXT)\b"
)


def _result(status, **fields):
    return dict(stage="M04_G1_CHART_PAIR_PREFLIGHT_READONLY",status=status,
                selected_pair=None,rest_requests=0,websocket_connections=0,
                execution_allowed=False,independent_g1_verified=False,
                complete_trade_tape_verified=False,
                sampled_ack_is_not_continuous_subscription=True,
                local_trade_activity_is_not_future_tick_guarantee=True) | fields


def failure_counts(records):
    counts=Counter()
    for r in records:
        msg=r.get("MESSAGE")
        if type(msg) is not str:
            continue
        found=set(_FAILURE.findall(msg))
        if found:
            counts.update(found)
        elif "M03_WS_WATCH_JSON_INVALID" in msg:
            counts["M03_WS_WATCH_JSON_INVALID_SUBTYPE_UNAVAILABLE"]+=1
    return dict(sorted(counts.items()))


def choose_pair(*, records, source_db, now):
    if type(now) is not int or now<1500000300:
        return _result("PREFLIGHT_CLOCK_INVALID")
    try:
        samples=_decode_records(records,now+1)
    except (ValueError,TypeError,UnicodeError,OverflowError):
        return _result("PREFLIGHT_CHECKPOINT_INVALID")
    details=dict(json_failure_journal_record_counts_last_hour=failure_counts(records))
    if len(samples)<2:
        return _result("PREFLIGHT_NEEDS_TWO_CHECKPOINTS",**details)
    a,b=samples[-2:]
    if (not 0<=now-b["observed_epoch"]<=60
            or not 0<b["observed_epoch"]-a["observed_epoch"]<=60
            or not a["pid"] or not a["invocation"]
            or a["pid"]!=b["pid"] or a["invocation"]!=b["invocation"]):
        return _result("PREFLIGHT_RECENT_CHECKPOINT_TRANSITION_OR_STALE",**details)
    if any("indexed_trade_pairs_acknowledged" not in r for r in (a,b)):
        return _result("PREFLIGHT_ACK_UNKNOWN",**details)
    pairs=sorted(p for p in set(a["indexed_trade_pairs_acknowledged"]) &
                 set(b["indexed_trade_pairs_acknowledged"]) if _valid_pair(p))
    details.update(latest_checkpoint_epoch=b["observed_epoch"],
                   preceding_checkpoint_epoch=a["observed_epoch"],
                   sampled_worker_pid=b["pid"],sampled_invocation=b["invocation"],
                   ack_intersection_pairs=pairs)
    if not pairs:
        return _result("PREFLIGHT_NO_ACK_INTERSECTION",**details)
    try:
        db=_open_source(source_db)
        try:
            db.execute("BEGIN")
            marks=",".join("?" for _ in pairs)
            cursor=db.execute(
                "SELECT pair,exchange_epoch,sequence,recorded_epoch "
                "FROM m03_ws3_trade_samples WHERE pair IN ("+marks+") "
                "AND exchange_epoch>=? AND exchange_epoch<? LIMIT ?",
                (*pairs,now-300,now,_MAX_ROWS+1))
            recent=Counter()
            five=Counter()
            last={}
            seen=set()
            for i,(pair,stamp,seq,receipt) in enumerate(cursor,1):
                if (i>_MAX_ROWS or pair not in pairs or type(stamp) is not int
                        or not now-300<=stamp<now or type(seq) is not int or seq<=0
                        or (pair,stamp,seq) in seen):
                    raise ValueError("PREFLIGHT_INDEX_INVALID")
                seen.add((pair,stamp,seq))
                if type(receipt) is not int or not stamp<=receipt<=now:
                    continue
                five[pair]+=1
                if stamp>=now-120:
                    recent[pair]+=1
                    last[pair]=max(last.get(pair,0),stamp)
        finally:
            db.close()
    except (OSError,ValueError,TypeError,sqlite3.Error,OverflowError):
        return _result("PREFLIGHT_LOCAL_SOURCE_UNAVAILABLE",**details)
    ranked=sorted((p for p in pairs if recent[p]>0),
                  key=lambda p:(-recent[p],-five[p],-last[p],p))
    details["active_ack_pair_candidates"]=[
        dict(pair=p,local_trade_rows_120s=recent[p],
             local_trade_rows_300s=five[p],last_trade_epoch=last[p])
        for p in ranked]
    if not ranked:
        return _result("PREFLIGHT_NO_RECENT_LOCAL_ACTIVITY",**details)
    return _result("PREFLIGHT_CURRENT_PAIR_REFERENCE_ONLY",
                   selected_pair=ranked[0],**details)


def main():
    now=int(time.time())
    try:
        report=choose_pair(records=_journal_read(now+1),
                           source_db=Path(DEFAULT_DB),now=now)
    except (OSError,ValueError,subprocess.SubprocessError):
        report=_result("PREFLIGHT_JOURNAL_UNAVAILABLE")
    print(json.dumps(report,sort_keys=True,separators=(",",":")))
    return 0 if report["status"]=="PREFLIGHT_CURRENT_PAIR_REFERENCE_ONLY" else 2


if __name__=="__main__":
    raise SystemExit(main())
