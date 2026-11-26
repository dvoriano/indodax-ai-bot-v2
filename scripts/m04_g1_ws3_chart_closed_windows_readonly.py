"""Prospective original WS3 corroboration, one explicit 15m or 1h session.

Reuse the proven chart-only collector and original raw/ACK readers. No
REST, reconnect, service action, extra scanner, source writes or G1 issuer.
Sampled ACK and contiguous observed chart offsets are not complete tape.
"""
from __future__ import annotations

import argparse
from decimal import InvalidOperation
import json
import os
import sqlite3
import subprocess
import sys
import time

from scripts.m04_g1_ws3_chart_tick_probe_readonly import (
    DEFAULT_DB, _LOCKS, _result, _valid_pair, collect_chart_ticks,
    compare_overlap, read_raw_overlap, _open_source, ChartObservationInterrupted,
)
from scripts.m04_g1_local_ack_windows_readonly import (
    _journal_read, summarize_local_ack_windows,
)
from scripts.m04_g1_chart_current_pair_preflight_readonly import (
    choose_pair, failure_counts,
)

M03="indodax-v2-m03-mainpath.service"
UNITS=(M03,"indodax-v2-public-ws-btc.service",
       "indodax-v2-m05-telegram.service")
DURATIONS={"15m":1020,"1h":3720}
WINDOWS={"15m":900,"1h":3600}


def _report(status, **fields):
    return dict(_result(status,**fields),
                stage="M04_G1_WS3_PROSPECTIVE_CLOSED_WINDOWS_RESEARCH_ONLY")


def _service(unit):
    cmd=["systemctl","--user","show",unit,"--no-pager"]
    for key in ("MainPID","NRestarts","ActiveState","SubState","InvocationID"):
        cmd.extend(("-p",key))
    return dict(row.split("=",1) for row in subprocess.check_output(
        cmd,text=True,timeout=5).splitlines() if "=" in row)


def _baseline(service):
    states={u:service(u) for u in UNITS}
    for state in states.values():
        if (state.get("ActiveState")!="active"
                or state.get("SubState")!="running"
                or not str(state.get("MainPID","")).isdigit()
                or int(state["MainPID"])<=0
                or not state.get("InvocationID")):
            raise ValueError("CHART_ORIGINAL_SERVICE_UNAVAILABLE")
    if states[M03].get("NRestarts")!="0":
        raise ValueError("CHART_M03_RESTART_REVIEW_REQUIRED")
    return states


def compare_closed_windows(*, capture, pair, horizon, source_db, records,
                           expected_invocation):
    """Use fixed closed exchange windows and receipt cutoff, not lookahead.

    A complete local observation duration does not imply full chart coverage.
    Report all observed rows within the closed window and a separately
    edge-bounded comparison; never substitute one for the other.
    """
    if horizon not in WINDOWS or not _valid_pair(pair):
        raise ValueError("CHART_CLOSED_SCOPE_INVALID")
    ack,finish=capture["ack_epoch"],capture["finish_epoch"]
    end=finish//60*60
    labels=("15m","1h") if horizon=="1h" else ("15m",)
    if ack+1>end-WINDOWS[horizon]:
        raise ValueError("CHART_CLOSED_WINDOW_NOT_SPANNED")
    ticks=capture["ticks"]
    receipts=capture["tick_receipts"]
    if (len(ticks)!=len(receipts)
            or any(t[:2] not in receipts or
                   not max(ack,t[0])<=receipts[t[:2]]<=finish for t in ticks)):
        raise ValueError("CHART_CLOSED_RECEIPTS_INVALID")
    ack_report=summarize_local_ack_windows(
        records=records,pairs=[pair],end_epoch=end)
    windows={}
    for label in labels:
        start=end-WINDOWS[label]
        observed=[t for t in ticks if start<=t[0]<end
                  and receipts[t[:2]]<=end]
        late=sum(start<=t[0]<end and receipts[t[:2]]>end for t in ticks)
        # Only bracketing on-time ticks provide observed chart interior.
        # No assumption that quiet seconds are missing or inactive.
        eligible=[t for t in ticks if receipts[t[:2]]<=end]
        a=max(start,ack+1,min((t[0]+1 for t in eligible),default=end))
        b=min(end,max((t[0] for t in eligible),default=start))
        local,unknown,local_late=read_raw_overlap(
            source_db,pair,start,end,end,max_identities=100000)
        sample=ack_report.get("windows",{}).get(label)
        # Existing aggregate counts do not carry invocation identity, so
        # run a separate membership check over the actual bounded records.
        from scripts.m04_g1_local_ack_windows_readonly import _decode_records
        decoded=[r for r in _decode_records(records,end)
                 if r["observed_epoch"]>=start]
        invocation_matches=bool(decoded) and all(
            r["invocation"]==expected_invocation for r in decoded)
        windows[label]=dict(
            window_start_epoch=start,window_end_epoch=end,
            chart_ack_to_finish_spans_closed_window=True,
            chart_exchange_interior_spans_full_window=(a==start and b==end),
            chart_rows_by_cutoff=len(observed),chart_late_rows=late,
            local_raw_unknown_receipts=unknown,local_raw_late_receipts=local_late,
            closed_window_observed_comparison=compare_overlap(
                {"ticks":observed},local,start=start,end=end),
            interior_comparison=compare_overlap(
                {"ticks":observed},local,start=a,end=max(a,b)),
            sampled_m03_ack=sample,
            all_sampled_m03_invocations_match=invocation_matches,
            complete_trade_tape_verified=False)
    return windows


def run(*, horizon, source_db=DEFAULT_DB, service=None, journal_read=None,
        ws_module=None, clock=None, monotonic=None, progress=None):
    if horizon not in DURATIONS or (progress is not None and not callable(progress)):
        return _report("CHART_CLOSED_SCOPE_INVALID",connection_attempts=0)
    if any(os.environ.get(k)!=v for k,v in _LOCKS.items()):
        return _report("CHART_EXPLICIT_NONLIVE_LOCKS_REQUIRED",connection_attempts=0)
    service=service or _service
    journal_read=journal_read or _journal_read
    clock=clock or time.time
    monotonic=monotonic or time.monotonic
    try:
        before=_baseline(service)
        invocation=before[M03]["InvocationID"]
        now=int(clock())
        records=journal_read(now+1)
        if failure_counts([r for r in records
                           if r.get("_SYSTEMD_INVOCATION_ID")==invocation]):
            raise ValueError("CHART_M03_JSON_FAILURE_REVIEW_REQUIRED")
        pre=choose_pair(records=records,source_db=source_db,now=now)
        if (pre.get("status")!="PREFLIGHT_CURRENT_PAIR_REFERENCE_ONLY"
                or pre.get("sampled_invocation")!=invocation
                or not _valid_pair(pre.get("selected_pair"))):
            raise ValueError("CHART_CURRENT_PAIR_PREFLIGHT_BLOCKED")
        pair=pre["selected_pair"]
        _open_source(source_db).close()
        if not 0<=int(clock())-now<=10:
            raise ValueError("CHART_PREFLIGHT_STALE")
        def health():
            if any(service(u)!=before[u] for u in UNITS):
                raise ValueError("CHART_ORIGINAL_SERVICE_CHANGED")
        health()
        if ws_module is None:
            import websocket as ws_module
    except KeyboardInterrupt:
        return _report("CHART_OBSERVATION_INTERRUPTED",connection_attempts=0,
                       horizon=horizon,completed_window_comparison=False)
    except (ValueError,OSError,TypeError,sqlite3.Error,subprocess.SubprocessError,ImportError):
        return _report("CHART_CLOSED_PREFLIGHT_BLOCKED",connection_attempts=0)
    capture=None
    try:
        if progress is not None:
            progress(dict(event="CHART_PREFLIGHT_READY",pair=pair,
                          requested_observation_seconds=DURATIONS[horizon]))
        capture=collect_chart_ticks(
            pair=pair,seconds=DURATIONS[horizon],ws_module=ws_module,
            monotonic=monotonic,epoch=clock,closed_window_research=True,
            health_check=health,progress=progress)
        health()
        if progress is not None:
            progress(dict(event="CHART_COLLECTION_FINISHED",pair=pair,
                          chart_ticks_observed=len(capture["ticks"]),
                          publication_count=capture["publication_count"]))
        end=capture["finish_epoch"]//60*60
        records=journal_read(end)
        windows=compare_closed_windows(
            capture=capture,pair=pair,horizon=horizon,source_db=source_db,
            records=records,expected_invocation=invocation)
        health()
    except ChartObservationInterrupted as exc:
        return _report("CHART_OBSERVATION_INTERRUPTED",connection_attempts=1,
                       horizon=horizon,completed_window_comparison=False,
                       partial_counters_only=True,**exc.summary)
    except KeyboardInterrupt:
        fields={} if capture is None else dict(
            chart_ticks_observed=len(capture["ticks"]),
            publication_count=capture["publication_count"],
            ack_epoch=capture["ack_epoch"],finish_epoch=capture["finish_epoch"])
        return _report("CHART_OBSERVATION_INTERRUPTED",connection_attempts=1,
                       horizon=horizon,pair=pair,completed_window_comparison=False,
                       partial_counters_only=True,**fields)
    except (ValueError,InvalidOperation) as exc:
        import re
        reason=str(exc)
        return _report("CHART_CLOSED_OBSERVATION_BLOCKED",connection_attempts=1,
                       pair=pair,reason=reason if re.fullmatch(
                           r"CHART_[A-Z0-9_]{1,100}",reason) else "CHART_SOURCE_INVALID")
    except Exception:
        return _report("CHART_CLOSED_OBSERVATION_UNAVAILABLE",connection_attempts=1,pair=pair)
    return _report("CHART_CLOSED_WINDOWS_RESEARCH_ONLY",connection_attempts=1,
                   pair=pair,horizon=horizon,original_services_unchanged=True,
                   chart_stream_epoch=capture["chart_stream_epoch"],
                   chart_ticks_observed=len(capture["ticks"]),
                   **{k:capture[k] for k in ("ack_epoch","finish_epoch",
                       "baseline_offset","last_offset","publication_count")},
                   windows=windows)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon",choices=tuple(DURATIONS),required=True)
    parser.add_argument("--observe-public-chart-closed-window",action="store_true",required=True)
    args=parser.parse_args(argv)
    def progress(fields):
        print(json.dumps(fields,sort_keys=True,separators=(",",":")),
              file=sys.stderr,flush=True)
    report=run(horizon=args.horizon,progress=progress)
    print(json.dumps(report,sort_keys=True,separators=(",",":")),flush=True)
    if report["status"]=="CHART_OBSERVATION_INTERRUPTED":
        return 130
    return 0 if report["status"]=="CHART_CLOSED_WINDOWS_RESEARCH_ONLY" else 2


if __name__=="__main__":
    raise SystemExit(main())
