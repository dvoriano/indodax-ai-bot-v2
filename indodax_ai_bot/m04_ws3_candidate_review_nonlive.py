"""Original two-candidate WS3 -> pre-AI diagnostic decision loop, NONLIVE.

This never substitutes for actual MARKET/RISK/STRATEGY responses, G1 or
ThreeRoleShadow's qualified scenario comparer; WATCH is not a paper BUY.
"""
from __future__ import annotations
from hashlib import sha256
import json
import re

PAIR=re.compile(r"[a-z0-9]+_idr\Z")
ROLES=("MARKET","RISK","STRATEGY")
DISPATCH="NOT_CALLED_G1_UNVERIFIED_AND_OWNER_PAID_AI_GO_ABSENT"


def assess_uncalled_ws3_candidate_loop(*, handoff: dict, now_epoch: int) -> dict:
    out={
        "stage":"M04_ORIGINAL_WS3_0_1_2_SOURCE_REVIEW_NONLIVE",
        "status":"RESCAN_UNTRUSTED_WATCH_INPUT",
        "watch_candidate_count":0,
        "qualified_role_decision_count":0,
        "selected_pair":None,
        "candidate_reviews":[],
        "role_calls_observed":0,
        "provider_calls":0,
        "paper_fill_authorized":False,
        "telegram_trade_preview_authorized":False,
        "actual_final_decision":None,
        "execution_allowed":False,
    }
    if (type(now_epoch) is not int or now_epoch<=0
            or type(handoff) is not dict
            or handoff.get("status") not in (
                "M04_WS3_HANDOFF_ROLE_INPUTS_PREPARED",
                "M04_WS3_HANDOFF_NO_CURRENT_WATCH")
            or handoff.get("g1_independent_issuer_verified") is not False
            or handoff.get("three_real_role_responses_observed") is not False
            or handoff.get("execution_allowed") is not False
            or handoff.get("actual_three_role_ai_calls",0)!=0
            or handoff.get("provider_http_calls")!=0):
        return out
    rows=handoff.get("candidates")
    if (type(rows) is not list or not 0<=len(rows)<=2
            or handoff.get("candidate_count")!=len(rows)):
        return dict(out,status="RESCAN_INVALID_WATCH_COHORT")
    if not rows:
        return dict(out,status="RESCAN_NO_CURRENT_WATCH")
    seen=set()
    for row in rows:
        try:
            pair=row["pair"]
            packet=row["public_evidence"]
            digest=row["evidence_digest_sha256"]
            if (type(pair) is not str or not PAIR.fullmatch(pair)
                    or pair in ("pepe_idr","floki_idr") or pair in seen
                    or type(packet) is not dict or packet["pair"]!=pair
                    or type(digest) is not str or not re.fullmatch("[a-f0-9]{64}",digest)
                    or row["execution_allowed"] is not False
                    or packet["execution_allowed"] is not False
                    or packet["complete_trade_tape_verified"] is not False
                    or packet["independent_g1_issuer_verified"] is not False
                    or packet["trade_side_aggressor_qualified"] is not False
                    or type(packet["observed_epoch"]) is not int
                    or not 0<=now_epoch-packet["observed_epoch"]<=150):
                raise ValueError("SOURCE_INVALID")
            canonical=sha256(json.dumps(
                packet,sort_keys=True,separators=(",",":"),ensure_ascii=True,
                allow_nan=False).encode("ascii")).hexdigest()
            slots=row["role_slots"]
            if (canonical!=digest or type(slots) is not list or len(slots)!=3
                    or [s.get("role") for s in slots]!=list(ROLES)
                    or any(s.get("evidence_digest_sha256")!=digest
                           or s.get("dispatch")!=DISPATCH
                           or s.get("model_output") is not None for s in slots)):
                raise ValueError("SOURCE_OR_ROLE_TAMPER")
        except (KeyError,ValueError,TypeError,UnicodeError,ArithmeticError):
            return dict(out,status="RESCAN_SOURCE_DIGEST_OR_ROLE_INVALID",
                        watch_candidate_count=0,candidate_reviews=[])
        seen.add(pair)
        indexed=packet.get("indexed_trade_window_evidence")
        source_status=(indexed.get("status") if type(indexed) is dict
                       else "NO_INDEXED_LOCAL_SOURCE")
        one=five=None
        if type(indexed) is dict and type(indexed.get("closed_windows")) is dict:
            one=indexed["closed_windows"].get("1m")
            five=indexed["closed_windows"].get("5m")
        count=one.get("locally_observed_trade_rows") if type(one) is dict else None
        count=count if type(count) is int and count>=0 else None
        volume=(five.get("observed_quote_idr_only")
                if type(five) is dict else None)
        if source_status=="INDEXED_LOCAL_EVIDENCE_INVALID_OR_UNREADABLE":
            reason="WAIT_CORRUPT_LOCAL_INDEX"
        elif count is None:
            reason="WAIT_NO_LOCAL_TRADE_WINDOW"
        elif count<2:
            reason="WAIT_SPARSE_OBSERVED_1M_TRADES"
        else:
            reason="WAIT_PARTIAL_SOURCE_AND_UNCALLED_ROLES"
        out["candidate_reviews"].append({
            "pair":pair,
            "evidence_digest_sha256":digest,
            "source_observed_epoch":packet["observed_epoch"],
            "observed_trades_1m":count,
            "observed_quote_5m_idr_only":volume,
            "source_status":source_status,
            "reason":reason,
            "role_outputs_missing":list(ROLES),
            "qualified_to_trade":False,
        })
    return dict(out,watch_candidate_count=len(rows),
                status=("WAIT_ONE_WATCH_NO_QUALIFIED_AI_REVIEW" if len(rows)==1
                        else "WAIT_TWO_WATCHES_NO_COMPARABLE_AI_REVIEWS"))
