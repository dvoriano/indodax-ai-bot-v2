"""M04-A: one source-bound MARKET→RISK→STRATEGY contract, SHADOW ONLY.

This module does not make provider/network requests and cannot authorize an
Indodax order. The role caller may be injected only by later M04 provider work.
Public M03-I evidence remains untrusted: an injected caller MUST NOT run.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from time import time
from typing import Any

from .contracts import FinalDecision
from .g1_receipt import G1ReceiptVerifier

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
                 clock: Callable[[], float] = time,
                 source_verifier: G1ReceiptVerifier | None = None) -> None:
        self._caller = caller
        self._clock = clock
        # No verifier is wired from production public market or env flags.
        self._source_verifier = source_verifier

    def _run_two_candidates(self, trace_id: str, bulk: dict[str, Any],
                            preview: list[str]) -> dict[str, Any]:
        """M04-T: two *independently* source-bound candidates, shadow only.

        Preflight BOTH receipt+digest pairs before ANY injected model call, so
        a valid first candidate cannot trigger billable work if the second
        lacks a trustworthy issuer. Public G1 never passes this gate.
        """
        if (len(preview) != 2 or any(not isinstance(p, str) for p in preview)
                or len(set(preview)) != 2):
            return _reject("INVALID_SOURCE_COHORT_SHAPE")
        if any(bulk.get(k) is not True for k in (
                "g1_verified", "source_trust_verified", "trade_history_complete")):
            return _reject("G1_PUBLIC_EVIDENCE_UNTRUSTED")
        receipts = bulk.get("g1_attestations")
        if not isinstance(receipts, dict) or set(receipts) != set(preview):
            return _reject("G1_ISSUER_RECEIPT_UNVERIFIED")
        now = int(self._clock())
        isolated = []
        for pair in preview:
            item = deepcopy(bulk)
            item["eligible_cohort"]["provisional_watchlist_preview"] = [pair]
            item["g1_attestation"] = receipts[pair]
            # Never forward another candidate's private issuer claim.
            item.pop("g1_attestations", None)
            isolated.append(item)
        preflight = ThreeRoleShadow(
            caller=None, clock=lambda: now, source_verifier=self._source_verifier,
        )
        verified = []
        for pair, item in zip(preview, isolated):
            result = preflight.run(trace_id, item)
            if result["reason"] != "M04_PROVIDER_NOT_CONFIGURED":
                return _reject(
                    result["reason"], digest=result.get("source_digest_sha256"),
                    candidate_pair=pair,
                )
            verified.append(result["source_digest_sha256"])
        if self._caller is None:
            return _reject("M04_PROVIDER_NOT_CONFIGURED")
        reports = []
        for pair, item, digest in zip(preview, isolated, verified):
            result = ThreeRoleShadow(
                caller=self._caller, clock=lambda: now,
                source_verifier=self._source_verifier,
            ).run(trace_id, item)
            # No receipt, digest, pair, or prior-role results may migrate
            # between independently processed candidates.
            reports.append({
                "pair": pair,
                "evidence_digest_sha256": digest,
                "reason": result["reason"],
                "completed_roles": result["completed_roles"],
                "role_results": result["role_results"],
            })
        result = _reject("M04_TWO_CANDIDATE_SHADOW_ONLY")
        result["candidate_reports"] = reports
        result["candidate_count"] = 2
        result["source_digests_by_pair"] = dict(zip(preview, verified))
        result["completed_role_calls"] = sum(
            len(x["completed_roles"]) for x in reports
        )
        # Even synthetic signed receipts and six model-shaped responses
        # cannot authorize Final PASS or execution in this milestone.
        return result

    def run(self, trace_id: str, bulk: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(trace_id, str) or not trace_id.strip():
            return _reject("INVALID_TRACE_ID")
        if (isinstance(bulk, dict)
                and isinstance(bulk.get("eligible_cohort"), dict)
                and isinstance(bulk["eligible_cohort"].get("provisional_watchlist_preview"), list)
                and len(bulk["eligible_cohort"]["provisional_watchlist_preview"]) == 2):
            return self._run_two_candidates(
                trace_id, bulk,
                bulk["eligible_cohort"]["provisional_watchlist_preview"],
            )
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
        # M03 final max-2 preview is quality/stage ranked, so its candidate
        # may sit below scalar-score top10. Prefer the SAME-cycle selected
        # evidence, not a historical or partial unrelated top10 row.
        selected_evidence = bulk.get("provisional_market_evidence")
        if isinstance(selected_evidence, list):
            matches = [
                item for item in selected_evidence if isinstance(item, dict)
                and item.get("pair") == source["pair"]
            ]
            pair_evidence = matches[0] if len(matches) == 1 else None
        else:
            # Legacy synthetic M04 tests still use scalar top10 only.
            raw_market_evidence = bulk.get("ranked_diagnostics_top10")
            pair_evidence = next(
                (item for item in raw_market_evidence
                 if isinstance(item, dict) and item.get("pair") == source["pair"]),
                None,
            ) if isinstance(raw_market_evidence, list) else None
        if (not isinstance(pair_evidence, dict)
                or pair_evidence.get("closed_1m_epoch") != epoch
                or pair_evidence.get("trade_authorized") is not False):
            return _reject("G1_MISSING_BOUND_MARKET_EVIDENCE",
                           candidate_pair=source["pair"])
        # Bind the actual candle/microstructure snapshot to the commitment,
        # rather than hashing only the scalar score.
        source["market_evidence"] = json.loads(json.dumps(pair_evidence))
        stable = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = sha256(stable.encode("utf-8")).hexdigest()
        # A local SHA digest never authenticates Indodax or a trusted issuer.
        if not all(source[k] for k in (
            "source_trust_verified", "g1_verified", "trade_history_complete",
            "root_g1_verified", "root_trade_history_complete", "root_source_trust_verified",
        )):
            return _reject("G1_PUBLIC_EVIDENCE_UNTRUSTED", digest=digest,
                           candidate_pair=source["pair"])
        # These boolean labels arrive in mutable JSON, so they are NOT
        # sufficient to authorize a role call. Require an independently
        # authenticated INTERNAL issuer receipt covering the evidence hash.
        if (self._source_verifier is None
                or not self._source_verifier.verify(
                    bulk.get("g1_attestation"),
                    trace_id=trace_id, pair=source["pair"],
                    closed_epoch=epoch, source_digest=digest, now=now,
                )):
            return _reject("G1_ISSUER_RECEIPT_UNVERIFIED", digest=digest,
                           candidate_pair=source["pair"])
        if self._caller is None:
            return _reject("M04_PROVIDER_NOT_CONFIGURED", digest=digest,
                           candidate_pair=source["pair"])

        inputs = {
            "source": source,
            "market_evidence": json.loads(json.dumps(pair_evidence)),
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
                role_input = json.loads(json.dumps({
                    **inputs,
                    "prior_role_results": validated,
                }))
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
            if role == "STRATEGY" and parsed["action"] == "BUY":
                # M04-U: BOTH thresholds are mandatory for any prospective
                # profitable entry, but estimates are NOT executable facts.
                # M06 authenticated spendable balance and M07 fee/slippage
                # preflight have not been connected. A synthetic signed G1
                # receipt + positive model PnL must never become BUY-ready.
                if (Decimal(parsed["projected_net_profit_idr"]) < Decimal("10000")
                        or Decimal(parsed["projected_net_roi_percent"]) < Decimal("2")):
                    return {
                        **_reject("STRATEGY_BUY_NET_TARGET_UNMET", digest=digest,
                                  roles=roles, candidate_pair=source["pair"]),
                        "role_results": validated,
                    }
                return {
                    **_reject("STRATEGY_BUY_PREFLIGHT_UNVERIFIED", digest=digest,
                              roles=roles, candidate_pair=source["pair"]),
                    "role_results": validated,
                }
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
