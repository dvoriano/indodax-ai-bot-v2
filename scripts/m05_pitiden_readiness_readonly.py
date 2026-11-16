"""Read-only PITIDEN source audit, independent of momentum-candidate arrivals."""
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import json
import sqlite3
import time

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    DEFAULT_DB, _owned_private_regular, _finite_number,
    inspect_original_m03_ws3_watch_readonly,
)
from indodax_ai_bot.m05_pitiden_premium_layout import (
    DEFAULT_EVIDENCE_DB, FRAMES, build_timeframes, count_green,
    eligible_premium_watch, read_closed_candles, read_native_previous_24h,
    read_price_references,
)
from indodax_ai_bot.m03_ws3_local_observations_nonlive import (
    read_observed_minute_trade_ohlc,
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
            for row in rows:
                coverage[row["tf"]]+=int(row["change"] is not None)
            trade_minute=c["observed_epoch"]//60*60-60
            partial_trade=read_observed_minute_trade_ohlc(
                evidence_path,c["pair"],trade_minute,now_epoch=now)
            fresh=0<=now-c["observed_epoch"]<=150 and 0<=now-minute<=150
            cards.append({"pair":c["pair"],"age_sec":now-c["observed_epoch"],
              "fresh_for_radar":fresh,"history_samples":len(refs),
              "cached_candles":len(candles),"native_24h":prev is not None,
              "partial_trade_ohlc":partial_trade,
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
        # A gate-only pass is not an actual publication: sender's original
        # momentum eligibility, source selection, cooldown/claim remain.
        out["gate_only_is_not_send_authority"]=True
        return out
    except (OSError,ValueError,sqlite3.Error):
        return dict(out,status="ORIGINAL_SOURCE_QUERY_UNAVAILABLE")


if __name__=="__main__":
    print(json.dumps(inspect_readiness(),sort_keys=True,indent=2))
