"""Read-only PITIDEN source audit, independent of momentum-candidate arrivals."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import json
import os
import re
import sqlite3
import time

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    DEFAULT_DB, PAIR_ID, _owned_private_regular, _finite_number,
    inspect_original_m03_ws3_watch_readonly,
)
from indodax_ai_bot.m05_pitiden_premium_layout import (
    DEFAULT_EVIDENCE_DB, FRAMES, build_timeframes, count_green,
    eligible_premium_watch, read_closed_candles, read_native_previous_24h,
    read_price_references, apply_ws3_trade_samples,
)
from indodax_ai_bot.scanner import MAIN_EXCLUDED_PAIRS
from indodax_ai_bot.m03_ws3_evidence_archive_nonlive import _private_owned_regular


def _read(path, sql, params=()):
    with closing(sqlite3.connect(Path(path).absolute().as_uri()+"?mode=ro",
                                 uri=True, timeout=2)) as db:
        db.execute("PRAGMA query_only=ON")
        return db.execute(sql,params).fetchall()


def inspect_readiness(*, now=None, db_path=DEFAULT_DB,
                      evidence_path=DEFAULT_EVIDENCE_DB):
    now=int(time.time()) if now is None else now
    out={"status":"PITIDEN_READONLY_SOURCE_AUDIT","now_epoch":now,
         "execution_allowed":False,"telegram_messages_sent":0,
         "exchange_requests_added":0,"service_changes":0}
    if not _owned_private_regular(Path(db_path)):
        return dict(out,status="ORIGINAL_DB_UNTRUSTED")
    try:
        buckets=_read(db_path,
            "SELECT minute_epoch,COUNT(DISTINCT pair),MAX(exchange_epoch) "
            "FROM m03_ws_summary_watch_minute WHERE minute_epoch BETWEEN ? AND ? "
            "GROUP BY minute_epoch ORDER BY minute_epoch DESC",
            (now-1800,now))
        out["watch_buckets_30m"]=[list(r) for r in buckets]
        if not buckets:
            return dict(out,status="NO_WATCH_BUCKET_IN_30M")
        minute=buckets[0][0]
        sample=_read(db_path,
            "SELECT pair,exchange_epoch,last_idr,rolling_24h_idr,public_offset "
            "FROM m03_ws_summary_watch_minute WHERE minute_epoch=? LIMIT 801",
            (minute,))
        if len(sample)>800:
            return dict(out,status="SOURCE_BOUND_EXCEEDED")
        out["latest_bucket_age_seconds"]=now-minute
        out["archive_available"]=False
        try:
            if _private_owned_regular(Path(evidence_path)):
                cycles=_read(evidence_path,
                    "SELECT minute_epoch,source_pairs_seen,source_eligible_count,"
                    "stale_count,low_turnover_count,invalid_count,dropped_by_cap "
                    "FROM ws3_source_cycles WHERE minute_epoch BETWEEN ? AND ? "
                    "AND recorded_local_epoch<=? ORDER BY minute_epoch DESC",
                    (now-1800,now,now))
                out["archive_cycles_30m"]=[list(r) for r in cycles]
                out["archive_available"]=True
                out["archive_native24_column"]=any(
                    r[1]=="previous_24h_idr" for r in _read(
                        evidence_path,"PRAGMA table_info(ws3_source_evidence)"))
        except (OSError,ValueError,sqlite3.Error):
            pass
        pairs=[]
        for pair,stamp,price,volume,offset in sample:
            p=_finite_number(price,positive=True)
            v=_finite_number(volume,positive=True)
            if (type(pair) is not str or pair in MAIN_EXCLUDED_PAIRS
                    or not pair.endswith("_idr") or type(stamp) is not int
                    or not -90<=stamp-minute<60 or stamp>now
                    or type(offset) is not int or offset<0
                    or p is None or v is None or v<Decimal("400000000")):
                continue
            pairs.append((v,{"pair":pair,"observed_epoch":stamp,
                              "latest_price_idr":str(p),"volume_24h_idr":str(v)}))
        pairs.sort(key=lambda x:(-x[0],x[1]["pair"]))
        out["liquid_pairs_latest_bucket"]=len(pairs)
        coverage=Counter()
        cards=[]
        for _,c in pairs[:10]:
            refs=read_price_references(c,db_path=db_path,now_epoch=now,
                    source_kind="WS3",evidence_path=evidence_path)
            candles=read_closed_candles(c,db_path=db_path,now_epoch=now)
            prev=read_native_previous_24h(c,evidence_path=evidence_path,now_epoch=now)
            rows=build_timeframes(c,refs,candles=candles,previous_24h=prev)
            rows=apply_ws3_trade_samples(c,rows,db_path=db_path,now_epoch=now)
            for row in rows:
                coverage[row["tf"]]+=int(row["change"] is not None)
            fresh=0<=now-c["observed_epoch"]<=150 and 0<=now-minute<=150
            cards.append({"pair":c["pair"],"age_sec":now-c["observed_epoch"],
              "fresh_for_radar":fresh,"history_samples":len(refs),
              "cached_candles":len(candles),"native_24h":prev is not None,
              "green":count_green(rows),"premium_gate_only":eligible_premium_watch(c,rows),
              "returns":{r["tf"]:{"pct":str(r["change"]) if r["change"] is not None else None,
                         "approx":r["approx"]} for r in rows}})
        out["coverage"]={tf:[coverage[tf],len(cards)] for tf,_ in FRAMES}
        out["tested_pairs"]=cards
        out["fresh_tested_pairs"]=sum(c["fresh_for_radar"] for c in cards)
        out["fresh_premium_gate_only"]=sum(c["fresh_for_radar"] and c["premium_gate_only"]
                                           for c in cards)
        snap=inspect_original_m03_ws3_watch_readonly(db_path=db_path,now_epoch=now)
        out["original_ws3_status"]=snap["status"]
        out["original_ws3_candidates"]=len(snap.get("candidates",[]))
        out["independent_liquid_ws3_candidates"]=len(snap.get("liquid_watch_candidates",[]))
        # A gate-only pass is not an actual publication: sender's original
        # momentum eligibility, source selection, cooldown/claim remain.
        out["gate_only_is_not_send_authority"]=True
        return out
    except (OSError,ValueError,sqlite3.Error):
        return dict(out,status="ORIGINAL_SOURCE_QUERY_UNAVAILABLE")


def inspect_named_pairs(*, pairs, now=None, db_path=DEFAULT_DB,
                        evidence_path=DEFAULT_EVIDENCE_DB):
    """Bounded read-only gate attribution for specified public pairs.

    Historical reconstruction from retained minute rows is not the exact
    original sender input or a delivery receipt. No new exchange requests.
    """
    now=int(time.time()) if now is None else now
    if (type(now) is not int or now<=0 or not 1<=len(pairs)<=10
            or len(set(pairs))!=len(pairs)
            or any(type(p) is not str or not PAIR_ID.fullmatch(p) for p in pairs)):
        raise ValueError("PITIDEN_INVALID_PAIR_SCOPE")
    result={"status":"PITIDEN_NAMED_PAIR_RECONSTRUCTION_READONLY",
            "asof_wib":datetime.fromtimestamp(now,ZoneInfo("Asia/Jakarta")).isoformat(),
            "historical_reconstruction_not_original_sender_receipt":True,
            "provider_calls":0,"exchange_requests_added":0,"execution_allowed":False,
            "pairs":[]}
    if not _owned_private_regular(Path(db_path)):
        return dict(result,status="ORIGINAL_DB_UNTRUSTED")
    snap=inspect_original_m03_ws3_watch_readonly(db_path=db_path,now_epoch=now)
    result["ws3_status"]=snap["status"]
    rank={c["pair"]:(i+1,c) for i,c in enumerate(snap.get("ranked_review_shortlist_top40",[]))}
    independent={c["pair"]:c for c in snap.get("liquid_watch_candidates",[])}
    archive_ok=_private_owned_regular(Path(evidence_path))
    from indodax_ai_bot.m08_strategy_a_nonlive import assess_ws3_priority_alert_offline
    for pair in pairs:
        row={"pair":pair}
        result["pairs"].append(row)
        if pair in MAIN_EXCLUDED_PAIRS:
            row["reason"]="EXCLUDED_PAIR"
            continue
        try:
            if archive_ok:
                rec=_read(evidence_path,
                    "SELECT p.source_status,p.last_idr,p.rolling_24h_idr,p.source_exchange_epoch "
                    "FROM ws3_source_evidence p JOIN ws3_source_cycles c USING(minute_epoch) "
                    "WHERE p.pair=? AND p.minute_epoch BETWEEN ? AND ? "
                    "AND c.recorded_local_epoch<=? ORDER BY p.minute_epoch DESC LIMIT 30",
                    (pair,now-1800,now,now))
                row["archive_30m_status_counts"]=dict(Counter(x[0] for x in rec))
            recent=_read(db_path,
                "SELECT minute_epoch,exchange_epoch,last_idr,rolling_24h_idr,public_offset "
                "FROM m03_ws_summary_watch_minute WHERE pair=? AND minute_epoch BETWEEN ? AND ? "
                "AND exchange_epoch<=? ORDER BY minute_epoch DESC LIMIT 30",
                (pair,now-1800,now,now))
            row["retained_watch_samples_30m"]=len(recent)
            if not recent:
                row["reason"]="NO_RETAINED_WATCH_SAMPLE"
                continue
            minute,stamp,raw,vol,offset=recent[0]
            price=_finite_number(raw,positive=True)
            volume=_finite_number(vol,positive=True)
            if (price is None or volume is None or type(stamp) is not int
                    or type(minute) is not int or type(offset) is not int
                    or offset<0 or not -90<=stamp-minute<60):
                row["reason"]="INVALID_LATEST_SAMPLE"
                continue
            row.update({"last_idr":str(price),"rolling_24h_idr":str(volume),
                        "age_seconds":now-stamp})
            c={"pair":pair,"observed_epoch":stamp,"latest_price_idr":str(price),
               "volume_24h_idr":str(volume)}
            refs=read_price_references(c,db_path=db_path,now_epoch=now,
                    source_kind="WS3",evidence_path=evidence_path)
            candles=read_closed_candles(c,db_path=db_path,now_epoch=now)
            prev=read_native_previous_24h(c,evidence_path=evidence_path,now_epoch=now)
            rows=build_timeframes(c,refs,candles=candles,previous_24h=prev)
            row.update({"green":count_green(rows),"known_returns":sum(
                x["change"] is not None for x in rows),
                "timeframes":[{"tf":x["tf"],"change_pct":str(x["change"]) if x["change"] is not None else None,
                               "close_idr":str(x["close_price"]) if x["close_price"] is not None else None,
                               "volume_idr":str(x["volume"]) if x["volume"] is not None else None,
                               "sampled":x["approx"],"volume_sampled":bool(x.get("volume_sampled"))} for x in rows]})
            independent_row=independent.get(pair)
            row["independent_4_green_gate"] = bool(independent_row and
                independent_row["observed_epoch"]==stamp and eligible_premium_watch(c,rows))
            row["independent_gate_not_delivery_proof"]=True
            if not (0<=now-stamp<=150 and 0<=now-minute<=150):
                row["reason"]="LATEST_WATCH_SAMPLE_STALE"
            elif volume<Decimal("400000000"):
                row["reason"]="BELOW_PREMIUM_VOLUME_FLOOR"
            elif pair not in rank:
                row["reason"]="NOT_IN_ORIGINAL_MOMENTUM_TOP40"
            else:
                i, candidate=rank[pair]
                row["momentum_rank"]=i
                row["observed_rise_pct"]=candidate["observed_rise_pct"]
                verdict=assess_ws3_priority_alert_offline(
                    rise_pct=Decimal(candidate["observed_rise_pct"]),
                    rolling_24h_idr=volume,
                    early_acceleration=candidate.get("momentum_phase")=="EARLY_ACCELERATION_WATCH")
                row["priority_gate_if_enabled"]=verdict["priority_alert_eligible"]
                row["reason"]=("BELOW_FOUR_GREEN" if count_green(rows)<4 else
                               "PREMIUM_GATE_MET_DELIVERY_NOT_PROVEN")
        except (OSError,ValueError,ArithmeticError,sqlite3.Error,KeyError,TypeError):
            row["reason"]="PAIR_SOURCE_QUERY_UNAVAILABLE"
    return result


def probe_recent_trade_coverage(*, pairs, client=None, clock=time.time):
    """Explicit public-only one-shot diagnostic; no market persistence.

    At most four GET attempts; on any failed admission/transport/contract,
    stop, including 403/429. Do not retry or fall back to another endpoint.
    """
    from indodax_ai_bot.public_market import (
        IndodaxPublicV2RecentTradesResearch,PublicDataError,
        summarize_v2_trade_page_research,
    )
    result={"status":"PITIDEN_PUBLIC_V2_TRADE_PROBE_DEFAULT_BLOCKED",
            "pairs":[],"public_get_invocations":0,"provider_calls":0,
            "execution_allowed":False,"orders_sent":0,"telegram_messages_sent":0,
            "runtime_market_data_written":False,"service_changes":0,
            "complete_trade_tape_verified":False}
    if (type(pairs) is not tuple or not 1<=len(pairs)<=4
            or any(type(p) is not str or len(p)>40 or not PAIR_ID.fullmatch(p)
                   or p in MAIN_EXCLUDED_PAIRS for p in pairs)
            or len(set(pairs))!=len(pairs)):
        return dict(result,reason="INVALID_PAIR_SCOPE")
    if any(os.environ.get(k)!=v for k,v in {
        "INDODAX_TRADING_ENABLED":"NO","INDODAX_ALLOW_LIVE_EXECUTION":"NO",
        "INDODAX_EXECUTION_MODE":"DRY_RUN"}.items()):
        return dict(result,reason="EXPLICIT_NONLIVE_FLAGS_REQUIRED")
    try:
        if client is None:
            client=IndodaxPublicV2RecentTradesResearch(timeout=5.0)
    except (ValueError,OSError):
        return dict(result,reason="PUBLIC_PROBE_CLIENT_UNAVAILABLE")
    if not isinstance(client,IndodaxPublicV2RecentTradesResearch):
        return dict(result,reason="PUBLIC_PROBE_CLIENT_INVALID")
    result["status"]="PITIDEN_PUBLIC_V2_TRADE_COVERAGE_PROBE"
    for pair in pairs:
        before=int(clock())
        result["public_get_invocations"]+=1
        try:
            payload=client.recent_trades_sample(pair)
            after=int(clock())
            out=summarize_v2_trade_page_research(
                payload,requested_epoch=before,received_epoch=after)
            result["pairs"].append({"pair":pair,"status":"PAGE_OBSERVED",
                "requested_epoch":before,"received_epoch":after,**out})
        except (PublicDataError,OSError,ArithmeticError,ValueError,TypeError) as exc:
            code=str(exc)
            if not isinstance(exc,PublicDataError) or not re.fullmatch(r"[A-Z0-9_]{1,100}",code):
                code="PUBLIC_PROBE_SOURCE_UNAVAILABLE"
            result["pairs"].append({"pair":pair,"status":"PROBE_STOPPED","reason":code})
            result["status"]="PITIDEN_PUBLIC_V2_TRADE_PROBE_STOPPED"
            break
    result["unattempted_pairs"]=list(pairs[len(result["pairs"]):])
    result["invocations_include_local_admission_denials"]=True
    return result


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--pairs",nargs="+")
    ap.add_argument("--probe-trades",action="store_true",help="One bounded public v2 GET per explicit pair; no runtime data writes")
    ap.add_argument("--at-wib",help="ISO date/time e.g. 2026-11-16T16:31:59")
    args=ap.parse_args()
    if args.probe_trades:
        if not args.pairs or args.at_wib:
            ap.error("--probe-trades requires --pairs and no historical as-of")
        print(json.dumps(probe_recent_trade_coverage(pairs=tuple(args.pairs)),sort_keys=True,indent=2))
        raise SystemExit(0)
    if args.at_wib and not args.pairs:
        ap.error("--at-wib requires --pairs")
    now=None
    if args.at_wib:
        stamp=datetime.fromisoformat(args.at_wib)
        if stamp.tzinfo is None:
            stamp=stamp.replace(tzinfo=ZoneInfo("Asia/Jakarta"))
        now=int(stamp.timestamp())
        if not 0<=int(time.time())-now<=72*3600:
            ap.error("as-of must be within past 72 hours")
    print(json.dumps(inspect_named_pairs(pairs=tuple(args.pairs),now=now)
          if args.pairs else inspect_readiness(),sort_keys=True,indent=2))

