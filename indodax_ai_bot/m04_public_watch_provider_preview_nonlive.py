"""M04 PUBLIC WATCH -> Sumopod-compatible 3-role REQUEST PREVIEW, OFFLINE.

Construct narrowly scoped requests using one validated original M03->M04
handoff, preserving PUBLIC_UNATTESTED source and all missing evidence.
NEVER send these requests: owner-paid-AI GO, source consent and durable cost
control are separately required. This is NOT qualified ThreeRoleShadow/G1,
not model output, not an order nor Telegram delivery.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any

from .m04_public_watch_research_readonly import (
    prepare_public_watch_research_readonly,
)
from .m04_radar_three_role_handoff_readonly import (
    _MISSING_FACTS, inspect_original_radar_three_role_handoff_readonly,
)
from .m05_radar_momentum_nonlive import DEFAULT_DB
from .sumopod_provider import MODEL_RE, _strict_json, SumopodProviderError
from .three_role_ai import ROLES

_OFFLINE = "M04_PUBLIC_WATCH_PROVIDER_CONTRACT_OFFLINE"
_ROLE_STANCES = {
    "MARKET": frozenset(("WATCH", "AVOID")),
    "RISK": frozenset(("NO_TRADE",)),
    "STRATEGY": frozenset(("WAIT",)),
}
_SYSTEM_PROMPTS = {
    "MARKET": (
        "PUBLIC WATCH research MARKET, not qualified G1 trading analysis. "
        "Use only the attached original public ticker snapshots. "
        "Never infer full trade flow, 1-minute executed volume, candlestick "
        "indicators, future rises, news, listing, entry/exit, or safe BUY. "
        "Choose stance WATCH or AVOID with a short bounded reason. "
        "A rising quote or rolling-24h turnover delta is NOT verified buy pressure."
    ),
    "RISK": (
        "PUBLIC WATCH research RISK, not authenticated account Risk. "
        "All 11 missing evidence categories remain missing. "
        "Choose only NO_TRADE, and explain source/account/fee limitations. "
        "Do not promote public evidence, signer credentials or BUY authorization."
    ),
    "STRATEGY": (
        "PUBLIC WATCH research STRATEGY, no executable quote or owner funds. "
        "Choose only WAIT. No numeric profit, ROI, targets, entry, quantity "
        "or exit order can be justified by this incomplete public source. "
        "Any future verified normal BUY requires BOTH >=10000 IDR NET AND "
        ">=2% NET on the same all-in transaction, without promising a trade."
    ),
}
_RESP_KEYS = frozenset((
    "role", "pair", "evidence_digest_sha256", "stance", "reason",
    "missing_evidence_acknowledged", "g1_verified", "execution_allowed",
))
_HEX = re.compile(r"^[a-f0-9]{64}$")


def _blocked(status: str) -> dict[str, Any]:
    return {
        "stage": "M04_PUBLIC_WATCH_PROVIDER_DRYRUN",
        "status": status,
        "candidate_count": 0,
        "request_preview_count": 0,
        "request_previews": [],
        "provider_http_calls": 0,
        "model_responses_observed": 0,
        "model_availability_verified": False,
        "owner_paid_ai_go_authorized": False,
        "real_provider_dispatch_allowed": False,
        "telegram_messages_sent": 0,
        "new_market_scanner_invocations": 0,
        "independent_g1_issuer_verified": False,
        "source_is_qualified_for_trading": False,
        "owner_final_decision": None,
        "execution_allowed": False,
        "order_sent": False,
    }


def _models(models_by_role: Mapping[str, str] | None) -> dict[str, str] | None:
    if models_by_role is None:
        return None
    if (not isinstance(models_by_role, Mapping)
            or set(models_by_role) != set(ROLES)
            or any(type(models_by_role[r]) is not str
                   or not MODEL_RE.fullmatch(models_by_role[r])
                   for r in ROLES)):
        raise ValueError("M04_PUBLIC_WATCH_INVALID_OWNER_MODEL_SELECTION")
    # IDs must be selected by the owner, never picked by this module.
    return {r: models_by_role[r] for r in ROLES}


def prepare_public_watch_provider_request_preview_offline(
    *, handoff: dict, now_epoch: int,
    models_by_role: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Prepare three *NOT DISPATCHABLE* request envelopes per candidate.

    The preview is not a provider call, authorization, or G1 credential.
    No API token, owner identifier, private balance or URL is included.
    """
    if type(now_epoch) is not int or now_epoch <= 0:
        return _blocked("M04_PUBLIC_WATCH_INVALID_CLOCK")
    try:
        models = _models(models_by_role)
    except (ValueError, TypeError):
        return _blocked("M04_PUBLIC_WATCH_INVALID_OWNER_MODEL_SELECTION")
    research = prepare_public_watch_research_readonly(
        handoff=handoff, now_epoch=now_epoch,
    )
    if research["status"] not in (
        "M04_PUBLIC_WATCH_RESEARCH_OFFLINE_ONLY",
        "M04_PUBLIC_WATCH_RESEARCH_NO_CURRENT_CANDIDATE",
    ):
        return _blocked("M04_PUBLIC_WATCH_SOURCE_OR_RESEARCH_UNVERIFIED")
    if (research["provider_http_calls"] != 0
            or research["execution_allowed"] is not False
            or research["order_sent"] is not False
            or research["independent_g1_issuer_verified"] is not False
            or research["three_real_role_responses_observed"] is not False
            or research["candidate_count"] != len(handoff["candidates"])):
        return _blocked("M04_PUBLIC_WATCH_RESEARCH_AUTHORITY_CONFLICT")
    result = _blocked(
        _OFFLINE if research["candidate_count"] else
        "M04_PUBLIC_WATCH_NO_CURRENT_CANDIDATE"
    )
    result["model_ids_supplied_by_caller"] = models is not None
    result["model_ids_confirmed_available_at_provider"] = False
    result["candidate_count"] = research["candidate_count"]
    previews: list[dict[str, Any]] = []
    for original, observed in zip(handoff["candidates"], research["candidates"]):
        packet = original["public_evidence"]
        digest = observed["evidence_digest_sha256"]
        if (original.get("pair") != observed["pair"]
                or original.get("evidence_digest_sha256") != digest
                or original.get("source_missing") != list(_MISSING_FACTS)):
            return _blocked("M04_PUBLIC_WATCH_RESEARCH_COHORT_MISMATCH")
        for role_index, role in enumerate(ROLES):
            context = {
                "contract": "M04_PUBLIC_WATCH_RESEARCH_UNTRUSTED_DATA_ONLY",
                "role": role,
                "pair": observed["pair"],
                "evidence_digest_sha256": digest,
                "public_market_evidence": packet,
                "source_missing": list(_MISSING_FACTS),
                "deterministic_pre_ai_research": (
                    observed["role_research_views"][role_index]
                ),
                "required_result_stance": sorted(_ROLE_STANCES[role]),
                "required_prior_roles": list(ROLES[:role_index]),
                # Prior model responses must be inserted by a separately
                # approved sequential caller, never invented here.
                "actual_prior_role_responses": None,
                "independent_g1_issuer_verified": False,
                "qualified_trade_proposal_authorized": False,
                "owner_paid_ai_go_authorized": False,
                "execution_allowed": False,
            }
            request = {
                "model": models[role] if models is not None else None,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPTS[role] +
                     " Return exactly JSON keys: role, pair, "
                     "evidence_digest_sha256, stance, reason, "
                     "missing_evidence_acknowledged, g1_verified, "
                     "execution_allowed. Copy missing evidence exactly, "
                     "keep g1_verified=false and execution_allowed=false. "
                     "Never return BUY, ALLOW or a price target."},
                    {"role": "user", "content": json.dumps(
                        context, sort_keys=True,
                        separators=(",", ":"), allow_nan=False,
                        ensure_ascii=True,
                    )},
                ],
                "stream": False,
            }
            preview = {
                "role": role,
                "pair": observed["pair"],
                "evidence_digest_sha256": digest,
                "model_id_from_caller": models[role] if models is not None else None,
                "depends_on_roles": list(ROLES[:role_index]),
                "request_body_preview": request,
                "owner_paid_ai_go_authorized": False,
                "send_allowed": False,
                "response_observed": False,
            }
            previews.append(preview)
    result["request_previews"] = previews
    result["request_preview_count"] = len(previews)
    return result


def prepare_ws3_micro_provider_request_preview_offline(
    *, handoff: dict, now_epoch: int,
    models_by_role: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Actual original WS3 input contract, still explicitly PUBLIC WATCH.

    The digest binds the partial observations, not the exchange or G1.
    This is a one-shot diagnostic request shape, not the qualified role
    contract or a permission to spend tokens.
    """
    from .scanner import MAIN_EXCLUDED_PAIRS
    fail = _blocked("M04_WS3_PROVIDER_PREVIEW_SOURCE_UNVERIFIED")
    if type(now_epoch) is not int or now_epoch <= 0:
        return fail
    try:
        models = _models(models_by_role)
        if (not isinstance(handoff, dict)
                or handoff.get("status") != "M04_WS3_MICRO_PREALERT_ROLE_INPUTS_PREPARED"
                or handoff.get("source") != "EXISTING_ORIGINAL_M03_WS3_DB_ONLY"
                or handoff.get("micro_prealert_source_review") is not True
                or handoff.get("actual_three_role_ai_calls") != 0
                or handoff.get("provider_http_calls") != 0
                or handoff.get("g1_independent_issuer_verified") is not False
                or handoff.get("execution_allowed") is not False):
            return fail
        rows = handoff["candidates"]
        if (type(rows) is not list or not 1 <= len(rows) <= 2
                or handoff.get("candidate_count") != len(rows)
                or handoff.get("three_role_input_slots_prepared") != 3*len(rows)):
            return fail
        previews, seen = [], set()
        for row in rows:
            pair, packet, digest = (row["pair"], row["public_evidence"],
                                    row["evidence_digest_sha256"])
            observed = packet["observed_epoch"]
            micro = packet["microstructure_source_evidence"]
            if (type(pair) is not str or not re.fullmatch(r"[a-z0-9]+_idr",pair)
                    or pair in MAIN_EXCLUDED_PAIRS or pair in seen
                    or row.get("execution_allowed") is not False
                    or packet["pair"] != pair
                    or packet["source_kind"] != "ORIGINAL_V2_WS3_MICRO_PREALERT_SOURCE_UNATTESTED"
                    or packet["execution_allowed"] is not False
                    or packet["complete_trade_tape_verified"] is not False
                    or packet["independent_g1_issuer_verified"] is not False
                    or packet["trade_side_aggressor_qualified"] is not False
                    or packet["closed_candles"] != "UNVERIFIED"
                    or type(observed) is not int or not 0 <= now_epoch-observed <= 90
                    or type(micro) is not dict
                    or micro.get("execution_allowed") is not False
                    or micro.get("source_malformed") is not False
                    or micro.get("trade_source_gap_or_truncation") is not False
                    or not (micro.get("trade_activity_observed") is True
                            or micro.get("orderbook_snapshot_observed") is True)
                    or Decimal(packet["rolling_24h_turnover_idr"]) < 400000000
                    or not isinstance(digest,str) or not _HEX.fullmatch(digest)
                    or sha256(json.dumps(packet,sort_keys=True,separators=(",",":"),
                              ensure_ascii=True,allow_nan=False).encode("ascii")).hexdigest()!=digest
                    or row["role_slots"] != [
                        {"role":role,"evidence_digest_sha256":digest,
                         "dispatch":_ROLE_DISPATCH,"model_output":None} for role in ROLES]):
                return fail
            seen.add(pair)
            # Keep the same missing-evidence contract: a partial book or
            # trade sample is NOT a verified full-depth or aggressor source.
            extra = {
                "MARKET":"Assess early versus extended momentum using observed price and partial trade/book evidence. Explain which available observations support or contradict WATCH, without claiming breakout confirmation, full buyer flow or future returns.",
                "RISK":"Assess observed spread, sample depth, gaps and liquidity limitations. Do not infer full order-book stability or authenticated account costs. Explain why NO_TRADE is binding.",
                "STRATEGY":"Use prior MARKET and RISK responses to explain WAIT, what fresh evidence would be needed for entry/exit and why time to profit is unknown. No price target or invented profit.",
            }
            for index,role in enumerate(ROLES):
                context={
                    "contract":"M04_WS3_MICRO_PUBLIC_WATCH_NOT_G1",
                    "role":role,"pair":pair,"evidence_digest_sha256":digest,
                    "public_market_evidence":packet,"source_missing":list(_MISSING_FACTS),
                    "required_prior_roles":list(ROLES[:index]),
                    "actual_prior_role_responses":None,
                    "independent_g1_issuer_verified":False,
                    "qualified_trade_proposal_authorized":False,
                    "owner_paid_ai_go_authorized":False,"execution_allowed":False,
                }
                system=(extra[role]+" Choose stance "+"/".join(sorted(_ROLE_STANCES[role]))+
                    ". Return exactly JSON keys: role, pair, evidence_digest_sha256, stance, reason, "
                    "missing_evidence_acknowledged, g1_verified, execution_allowed. Copy source_missing "
                    "exactly, keep g1_verified=false and execution_allowed=false. Never return BUY, ALLOW or a price target.")
                previews.append({"role":role,"pair":pair,"evidence_digest_sha256":digest,
                    "model_id_from_caller":models[role] if models else None,
                    "depends_on_roles":list(ROLES[:index]),
                    "request_body_preview":{"model":models[role] if models else None,
                        "messages":[{"role":"system","content":system},
                                    {"role":"user","content":json.dumps(context,sort_keys=True,
                                          separators=(",",":"),allow_nan=False,ensure_ascii=True)}],
                        "stream":False},
                    "owner_paid_ai_go_authorized":False,"send_allowed":False,
                    "response_observed":False})
        return dict(_blocked(_OFFLINE),candidate_count=len(rows),
                    request_preview_count=len(previews),request_previews=previews,
                    source_contract="WS3_MICRO_PUBLIC_WATCH_NOT_G1")
    except (ValueError,TypeError,KeyError,ArithmeticError,UnicodeError):
        return fail


def validate_public_watch_provider_response_shape_offline(
    *, raw_response_json: str,
    request_preview: dict[str, Any],
) -> dict[str, Any]:
    """Shape-only validation, never claims a genuine provider HTTP response.

    The model response must be independently attributed to a real, explicitly
    owner-authorized request in a future separate runtime. Here it is text.
    """
    fail = {
        "status": "M04_PUBLIC_WATCH_MODEL_SHAPE_REJECTED",
        "response_shape_valid": False,
        "actual_provider_response_verified": False,
        "provider_http_calls": 0,
        "owner_paid_ai_go_authorized": False,
        "execution_allowed": False,
        "order_sent": False,
    }
    if not isinstance(request_preview, dict):
        return fail
    role = request_preview.get("role")
    pair = request_preview.get("pair")
    digest = request_preview.get("evidence_digest_sha256")
    if (role not in ROLES or type(pair) is not str
            or type(digest) is not str or not _HEX.fullmatch(digest)
            or request_preview.get("send_allowed") is not False):
        return fail
    try:
        parsed = _strict_json(raw_response_json)
    except (SumopodProviderError, TypeError):
        return fail
    if (set(parsed) != _RESP_KEYS
            or parsed.get("role") != role
            or parsed.get("pair") != pair
            or parsed.get("evidence_digest_sha256") != digest
            or type(parsed.get("stance")) is not str
            or parsed["stance"] not in _ROLE_STANCES[role]
            or type(parsed.get("reason")) is not str
            or not 1 <= len(parsed["reason"]) <= 600
            or type(parsed.get("missing_evidence_acknowledged")) is not list
            or parsed["missing_evidence_acknowledged"] != list(_MISSING_FACTS)
            or parsed.get("g1_verified") is not False
            or parsed.get("execution_allowed") is not False):
        return fail
    return {
        **fail,
        "status": "M04_PUBLIC_WATCH_MODEL_SHAPE_VALIDATED_OFFLINE",
        "response_shape_valid": True,
        # No trusted model origin, provider network, or cost proof here.
        "role": role,
        "pair": pair,
        "evidence_digest_sha256": digest,
        "observational_stance": parsed["stance"],
        "no_trade_override_allowed": False,
        "actual_provider_response_verified": False,
    }


def inspect_original_public_watch_provider_preview_offline(
    *, db_path: Path = DEFAULT_DB,
    now_epoch: int | None = None,
    models_by_role: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """ONE original inspector call, zero paid requests, no runtime wiring."""
    now = int(time.time()) if now_epoch is None else now_epoch
    if type(now) is not int or now <= 0:
        return _blocked("M04_PUBLIC_WATCH_INVALID_CLOCK")
    handoff = inspect_original_radar_three_role_handoff_readonly(
        now_epoch=now, db_path=db_path,
    )
    return prepare_public_watch_provider_request_preview_offline(
        handoff=handoff, now_epoch=now, models_by_role=models_by_role,
    )


def main() -> int:
    result = inspect_original_public_watch_provider_preview_offline()
    print(json.dumps({
        "status": result["status"],
        "candidate_count": result["candidate_count"],
        "request_preview_count": result["request_preview_count"],
        "roles": [p["role"] for p in result["request_previews"]],
        "provider_http_calls": result["provider_http_calls"],
        "model_responses_observed": result["model_responses_observed"],
        "owner_paid_ai_go_authorized": result["owner_paid_ai_go_authorized"],
        "execution_allowed": result["execution_allowed"],
        "order_sent": result["order_sent"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
