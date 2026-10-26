"""M04-A: one source-bound MARKET→RISK→STRATEGY contract, SHADOW ONLY.

This module does not make provider/network requests and cannot authorize an
Indodax order. The role caller may be injected only by later M04 provider work.
Public M03-I evidence remains untrusted: an injected caller MUST NOT run.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from time import time
from typing import Any

from .contracts import FinalDecision

ROLES = ("MARKET", "RISK", "STRATEGY")


def _reject(reason: str, *, digest: str | None = None, roles: list[str] | None = None,
            candidate_pair: str | None = None) -> dict[str, Any]:
    return {
        "stage": "M04A_THREE_ROLE_SHADOW",
        "status": "REJECT",
        "reason": reason,
        "source_digest_sha256": digest,
        "candidate_pair": candidate_pair,
        "completed_roles": roles if roles is not None else [],
        "expected_roles": list(ROLES),
        "role_results": [],
        "g1_verified": False,
        "provider_connected": False,
        "risk_veto_bypass_allowed": False,
        "final_decision": FinalDecision.REJECT.value,
        "g2_candidate_authorized": False,
        "execution_allowed": False,
        "live_trading_authorized": False,
    }


def _finite_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("INVALID_" + name)
    try:
        d = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("INVALID_" + name) from exc
    if not d.is_finite() or d < 0:
        raise ValueError("INVALID_" + name)
    return d


class ThreeRoleShadow:
    """One non-live AI boundary connected to the single bulk orchestrator.

    Caller is intentionally absent in production. An explicit caller is only
    used in regression tests to validate three-role output contracts.
    """

    def __init__(self, *, caller: Callable[[str, dict[str, Any]], Mapping[str, Any]] | None = None,
                 clock: Callable[[], float] = time) -> None:
        self._caller = caller
        self._clock = clock

    def run(self, trace_id: str, bulk: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(trace_id, str) or not trace_id.strip():
            return _reject("INVALID_TRACE_ID")
        if not isinstance(bulk, dict) or not isinstance(bulk.get("eligible_cohort"), dict):
            return _reject("MISSING_SOURCE_COHORT")
        cohort = bulk["eligible_cohort"]
        if cohort.get("status") != "ALIGNED_PUBLIC_UNATTESTED":
            return _reject("UNALIGNED_OR_INCOMPLETE_COHORT")
        pairs = cohort.get("ranked_same_window_diagnostics")
        preview = cohort.get("provisional_watchlist_preview")
        epoch = cohort.get("closed_1m_epoch")
        observed = bulk.get("screening_observed_epoch")
        if (not isinstance(pairs, list) or not isinstance(preview, list)
                or len(preview) > 2 or len(set(map(str, preview))) != len(preview)
                or isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0
                or isinstance(observed, bool) or not isinstance(observed, int) or observed < epoch
                or observed - epoch > 120):
            return _reject("INVALID_SOURCE_COHORT_SHAPE")
        indexed: dict[str, int] = {}
        for item in pairs:
            if not isinstance(item, dict):
                return _reject("INVALID_SOURCE_CANDIDATE")
            p, score = item.get("pair"), item.get("score")
            if (not isinstance(p, str) or not p.endswith("_idr")
                    or isinstance(score, bool) or not isinstance(score, int)
                    or not 0 <= score <= 100 or p in indexed):
                return _reject("INVALID_SOURCE_CANDIDATE")
            indexed[p] = score
        if not all(isinstance(p, str) and p in indexed for p in preview):
            return _reject("INVALID_PREVIEW_PAIR")
        reviews = cohort.get("provisional_quality_reviews")
        if not isinstance(reviews, list):
            return _reject("MISSING_PUBLIC_QUALITY_REVIEWS")
        quality: dict[str, bool] = {}
        for review in reviews:
            if (not isinstance(review, dict)
                    or not isinstance(review.get("pair"), str)
                    or review["pair"] in quality
                    or not isinstance(review.get("review_flags"), list)
                    or not isinstance(review.get("clear_of_provisional_cautions"), bool)):
                return _reject("INVALID_QUALITY_REVIEW")
            quality[review["pair"]] = (
                review["clear_of_provisional_cautions"]
                and review["review_flags"] == []
            )
        if any(not quality.get(p, False) for p in preview):
            return _reject("PREVIEW_HAS_UNRESOLVED_QUALITY_CAUTION")
        if not preview:
            return _reject("NO_PROVISIONAL_CANDIDATE")
        now = int(self._clock())
        if now < observed or now - observed > 60:
            return _reject("EXPIRED_G1_SOURCE")
        source = {
            "trace_id": trace_id,
            "pair": preview[0],
            "closed_1m_epoch": epoch,
            "screening_observed_epoch": observed,
            "btc_regime": cohort.get("btc_regime"),
            "pair_score": indexed[preview[0]],
            "provisional_quality_reviews": cohort.get("provisional_quality_reviews", []),
            "source_trust_verified": cohort.get("source_trust_verified") is True,
            "g1_verified": cohort.get("g1_verified") is True,
            "trade_history_complete": cohort.get("trade_history_complete") is True,
            "root_g1_verified": bulk.get("g1_verified") is True,
            "root_trade_history_complete": bulk.get("trade_history_complete") is True,
            "root_source_trust_verified": bulk.get("source_trust_verified") is True,
        }
        stable = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = sha256(stable.encode("utf-8")).hexdigest()
        # SHA-256 binds local bytes; it NEVER authenticates an exchange or
        # substitutes for a trusted G1 issuer. All three source gates required.
        if not all(source[k] for k in (
            "source_trust_verified", "g1_verified", "trade_history_complete",
            "root_g1_verified", "root_trade_history_complete", "root_source_trust_verified",
        )):
            return _reject("G1_PUBLIC_EVIDENCE_UNTRUSTED", digest=digest,
                           candidate_pair=source["pair"])
        if self._caller is None:
            return _reject("M04_PROVIDER_NOT_CONFIGURED", digest=digest,
                           candidate_pair=source["pair"])

        inputs = {
            "source": source,
            "evidence_digest_sha256": digest,
            "role_order": list(ROLES),
            "live_execution_enabled": False,
        }
        roles: list[str] = []
        validated: list[dict[str, Any]] = []
        for role in ROLES:
            if role == "STRATEGY" and validated[1]["decision"] != "ALLOW":
                break
            try:
                # Provider must never receive a mutable reference shared with
                # the other roles; local source digest stays bound to each input.
                role_input = json.loads(json.dumps(inputs))
                raw = self._caller(role, role_input)
                if not isinstance(raw, Mapping):
                    raise ValueError("MALFORMED_RESPONSE")
                if raw.get("role") != role or raw.get("pair") != source["pair"] or raw.get("evidence_digest_sha256") != digest:
                    raise ValueError("ROLE_PAIR_OR_EVIDENCE_MISMATCH")
                reason = raw.get("reason")
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
                    raise ValueError("INVALID_ROLE_REASON")
                if role == "MARKET":
                    bias = raw.get("bias")
                    if bias not in ("BULLISH", "BEARISH", "MIXED"):
                        raise ValueError("INVALID_MARKET_BIAS")
                    parsed = {"role": role, "bias": bias, "reason": reason}
                elif role == "RISK":
                    decision = raw.get("decision")
                    if decision not in ("ALLOW", "NO_TRADE", "EXIT_REVIEW"):
                        raise ValueError("INVALID_RISK_VETO")
                    parsed = {"role": role, "decision": decision, "reason": reason}
                else:
                    action = raw.get("action")
                    if action not in ("BUY", "WAIT", "EXIT_REVIEW"):
                        raise ValueError("INVALID_STRATEGY_ACTION")
                    parsed = {
                        "role": role, "action": action, "reason": reason,
                        "projected_net_profit_idr": str(_finite_decimal(
                            raw.get("projected_net_profit_idr"), "NET_PROFIT")),
                        "projected_net_roi_percent": str(_finite_decimal(
                            raw.get("projected_net_roi_percent"), "NET_ROI")),
                    }
            except Exception:
                return _reject("M04_ROLE_INVALID_OR_UNAVAILABLE", digest=digest,
                               roles=roles, candidate_pair=source["pair"])
            validated.append(parsed)
            roles.append(role)
            if role == "RISK" and parsed["decision"] != "ALLOW":
                return {
                    **_reject("RISK_VETO_" + parsed["decision"], digest=digest,
                              roles=roles, candidate_pair=source["pair"]),
                    "role_results": validated,
                }
        return {
            **_reject("M04_SHADOW_ONLY_FINAL_AND_PREFLIGHT_ABSENT",
                      digest=digest, roles=roles, candidate_pair=source["pair"]),
            "role_results": validated,
        }
