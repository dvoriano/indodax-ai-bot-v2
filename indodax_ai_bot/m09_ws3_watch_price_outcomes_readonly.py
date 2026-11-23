"""Retrospective WS3 WATCH price-path observations, NOT PAPER fills or profit.

Read actual ORIGINAL M03 five-second local price index only. A candidate
without observed future price is UNKNOWN, not a loser or zero. Never infer
real fills, fees, liquidity, source completeness, trading signals or G1.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
import stat
import re

from .m04_ws3_candidate_review_nonlive import assess_uncalled_ws3_candidate_loop

_HORIZONS = (5, 15, 30, 60)
_MAX_DELAY_SECONDS = 30
_SAFE = re.compile(r"[0-9a-f]{64}\Z")


def _price(value):
    if type(value) is not str or not 1 <= len(value) <= 64:
        raise ValueError("M09_WS3_PRICE_UNTRUSTED")
    try:
        d=Decimal(value)
    except InvalidOperation:
        raise ValueError("M09_WS3_PRICE_UNTRUSTED") from None
    if not d.is_finite() or d<=0 or d>=Decimal("1e25"):
        raise ValueError("M09_WS3_PRICE_UNTRUSTED")
    return d


def inspect_m09_ws3_watch_price_outcomes_readonly(
        *, handoff: dict, db_path: Path, evaluation_epoch: int) -> dict:
    """Mature 5/15/30/60m observed returns from a prior immutable M04 input.

    The original M04 handoff was created when a watch was fresh. We
    revalidate its digest/uncalled role slots *at that source time*, not by
    pretending it remains fresh for a BUY an hour later. Outcomes are a
    retrospective, read-only research reference, never model performance.
    """
    out = {
        "stage":"M09_ORIGINAL_WS3_WATCH_PRICE_OUTCOMES_READONLY",
        "status":"SOURCE_UNTRUSTED",
        "candidate_count":0,
        "observations":[],
        "horizons_minutes":list(_HORIZONS),
        "future_price_is_only_local_observed":True,
        "missing_future_prices_are_unknown":True,
        "complete_trade_tape_verified":False,
        "independent_g1_verified":False,
        "real_role_responses_observed":False,
        "paper_fill_authorized":False,
        "paper_orders_created":0,
        "virtual_pnl_calculated":False,
        "actual_pnl_calculated":False,
        "execution_allowed":False,
    }
    if (type(evaluation_epoch) is not int or evaluation_epoch<=0
            or type(handoff) is not dict
            or handoff.get("status")!="M04_WS3_HANDOFF_ROLE_INPUTS_PREPARED"
            or handoff.get("candidate_count") not in (1,2)
            or type(handoff.get("candidates")) is not list
            or len(handoff["candidates"])!=handoff["candidate_count"]):
        return out
    try:
        newest=max(row["public_evidence"]["observed_epoch"]
                   for row in handoff["candidates"])
        if type(newest) is not int or newest>evaluation_epoch:
            return out
        original = assess_uncalled_ws3_candidate_loop(
            handoff=handoff,now_epoch=newest)
        if (original.get("watch_candidate_count")!=handoff["candidate_count"]
                or not original.get("status","").startswith("WAIT_")
                or original.get("qualified_role_decision_count")!=0):
            return out
        seeds=[]
        for row in handoff["candidates"]:
            packet=row["public_evidence"]
            observed=packet["observed_epoch"]
            baseline=_price(packet["price_after_idr"])
            digest=row["evidence_digest_sha256"]
            if (not 0<observed<=evaluation_epoch
                    or not _SAFE.fullmatch(digest)):
                return out
            seeds.append((row["pair"],observed,baseline,digest))
    except (KeyError,TypeError,ValueError,ArithmeticError):
        return out
    p=Path(db_path)
    if (p.is_symlink() or not p.is_file()
            or not stat.S_ISREG(p.stat().st_mode)
            or p.stat().st_mode & 0o077):
        return dict(out,status="SOURCE_FILE_UNTRUSTED")
    try:
        with sqlite3.connect(p.resolve().as_uri()+"?mode=ro",
                             uri=True,timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            if not db.execute("""SELECT 1 FROM sqlite_master WHERE
                    type='table' AND name='m03_ws3_price_samples_5s'"""
                    ).fetchone():
                return dict(out,status="SOURCE_INDEX_NOT_AVAILABLE")
            samples=[]
            for pair,observed,baseline,digest in seeds:
                points={}
                for minutes in _HORIZONS:
                    horizon=observed+60*minutes
                    row={
                        "horizon_minutes":minutes,
                        "target_epoch":horizon,
                        "status":"NOT_YET_MATURED" if evaluation_epoch<horizon
                                 else "UNKNOWN_NO_LOCAL_PRICE_NEAR_HORIZON",
                        "observed_price_epoch":None,
                        "observed_price_idr":None,
                        "observed_gross_price_return_percent":None,
                        "sample_delay_seconds":None,
                        "fees_slippage_and_executable_depth_verified":False,
                    }
                    if evaluation_epoch>=horizon:
                        found=db.execute("""SELECT exchange_epoch,last_idr,
                            recorded_epoch FROM m03_ws3_price_samples_5s
                            WHERE pair=? AND exchange_epoch BETWEEN ? AND ?
                            AND recorded_epoch<=? ORDER BY exchange_epoch ASC
                            LIMIT 1""",(
                            pair,horizon,min(evaluation_epoch,horizon+_MAX_DELAY_SECONDS),
                            evaluation_epoch,
                        )).fetchone()
                        if found:
                            timestamp,raw,receipt=found
                            try:
                                if (type(timestamp) is not int
                                        or type(receipt) is not int
                                        or not horizon<=timestamp<=min(
                                            evaluation_epoch,horizon+_MAX_DELAY_SECONDS)
                                        or receipt>evaluation_epoch):
                                    raise ValueError("M09_WS3_PRICE_CLOCK")
                                price=_price(raw)
                                change=(price/baseline-1)*100
                                row.update(
                                    status="OBSERVED_LOCAL_GROSS_PRICE_CHANGE_ONLY",
                                    observed_price_epoch=timestamp,
                                    observed_price_idr=str(price),
                                    observed_gross_price_return_percent=str(change),
                                    sample_delay_seconds=timestamp-horizon,
                                )
                            except (TypeError,ValueError,ArithmeticError):
                                row["status"]="UNKNOWN_INVALID_OBSERVED_PRICE"
                    points[str(minutes)+"m"]=row
                samples.append({
                    "pair":pair,
                    "evidence_digest_sha256":digest,
                    "source_observed_epoch":observed,
                    "source_price_idr":str(baseline),
                    "role_review_status":"UNCALLED_WAIT_ONLY",
                    "observed_outcomes":points,
                    "market_fills_or_tradable_return_inferred":False,
                })
        return dict(out,status="LOCAL_PRICE_PATH_RESEARCH_ONLY",
                    candidate_count=len(samples),observations=samples)
    except sqlite3.Error:
        return dict(out,status="SOURCE_SQLITE_READ_UNAVAILABLE")
